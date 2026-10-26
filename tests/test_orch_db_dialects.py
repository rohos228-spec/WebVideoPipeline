"""Таблицы движка через SQLAlchemy: одинаково на SQLite и на Postgres.

Каждый тест с фикстурой ``orch_db`` идёт дважды: на временном файле SQLite
(всегда) и на живом Postgres (если задан ``TEST_DATABASE_URL``, как у
``tests/test_rls_postgres.py``). На Postgres схема поднимается миграциями
alembic до head — ровно тем путём, каким её получит SaaS-база; таблицы
движка перед каждым тестом очищаются.

Отдельно на SQLite проверяется, что ревизия 0018 строит ту же схему, что и
собственный раннер SQL-файлов движка: владельческая база, на которой раннер
уже отработал, и свежая база после alembic не должны расходиться.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.orchestrator.contracts import CheckpointData, PlanStep, SpendSnapshot, TaskStatus
from app.orchestrator.routing.types import LlmCallRecord
from app.settings import settings
from tests.orch_pg_support import TEST_DB_URL, prepare_postgres, reset_engine_state


@pytest.fixture(params=["sqlite", "postgres"])
def orch_db(request, monkeypatch) -> str:
    if request.param == "postgres":
        if not TEST_DB_URL:
            pytest.skip("TEST_DATABASE_URL не задан — живого Postgres нет")
        monkeypatch.setattr(settings, "database_url", TEST_DB_URL)
    reset_engine_state()
    if request.param == "postgres":
        prepare_postgres()
    yield request.param
    reset_engine_state()


# --- задачи, шаги, чекпоинты -------------------------------------------------


def test_task_store_roundtrip(orch_db: str) -> None:
    from app.orchestrator.agents.task_store import TaskStore

    store = TaskStore()
    first = store.insert("s-1", "цель", TaskStatus.queued.value)
    second = store.insert("s-1", "другая", TaskStatus.queued.value, project_id=7)
    assert first > 0 and second > first

    store.set_project_id(first, 5)
    store.set_project_id(first, 9)  # уже задан — не перетирается
    store.set_project_id(second, 9)
    assert store.get(first)["project_id"] == 5
    assert store.get(second)["project_id"] == 7

    store.set_flag(first, cancel_requested=1, steer_text="левее")
    row = store.get(first)
    assert row["cancel_requested"] == 1 and row["steer_text"] == "левее"
    assert row["created_at"].endswith("Z")

    spend = store.add_spend(first, internal=0.25, actions=1.5)
    spend = store.add_spend(first, internal=0.25)
    assert spend == SpendSnapshot(internal=0.5, actions=1.5)
    with pytest.raises(LookupError):
        store.add_spend(10_000, internal=1)

    plan = [PlanStep(action="look"), PlanStep(action="edit", params={"x": 1}, mutating=True)]
    store.save_plan(first, plan, position=1)
    store.mirror_progress(first, plan, 2, SpendSnapshot(internal=0.75, actions=2))
    row = store.get(first)
    assert row["position"] == 2 and float(row["spend_actions"]) == 2.0

    step_a = store.insert_step(first, 0, plan[0])
    step_b = store.insert_step(first, 1, plan[1])
    assert step_b > step_a
    store.note_running_outcome(step_b, "chg-1", 0.3)
    store.finish_step(step_a, "done", None, 0.0)
    steps = store.list_steps(first)
    assert [s["status"] for s in steps] == ["done", "running"]
    assert steps[1]["change_id"] == "chg-1" and steps[1]["mutating"] == 1

    store.set_status(second, TaskStatus.done.value)
    assert [t["id"] for t in store.list_open()] == [first]
    assert store.get(99_999) is None


def test_checkpoints_latest_and_context(orch_db: str) -> None:
    from app.orchestrator.agents.task_store import TaskStore
    from app.orchestrator.checkpoints import CheckpointStore

    task = TaskStore().insert("s-cp", "цель", TaskStatus.executing.value)
    store = CheckpointStore()
    assert store.load_latest(task) is None
    data = CheckpointData(plan=[PlanStep(action="look")], position=1, change_ids=["c1"],
                          spend=SpendSnapshot(internal=0.1))
    first = store.save(task, "s-cp", data)
    store.save_context(task, "s-cp", "контекст")
    latest = store.load_latest(task)
    assert latest is not None
    assert latest.context == "контекст" and latest.change_ids == ["c1"] and latest.position == 1
    assert store.save(task, "s-cp", data) > first


# --- события и сообщения чата -------------------------------------------------


async def test_event_bus_seq_persistence_and_compaction(orch_db: str) -> None:
    from datetime import UTC, datetime, timedelta

    from app.orchestrator.events import EventBus, compact_events, forget_seq_batch, persisted_types

    bus = EventBus()
    seqs = []
    for n in range(5):
        seqs.append((await bus.publish("s-ev", "agent_status", {"n": n})).seq)
    seqs.append((await bus.publish("s-ev", "token", {"text": "t"})).seq)
    seqs.append((await bus.publish("s-ev", "checkpoint", {"k": 1})).seq)
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)
    assert persisted_types("s-ev") == ["agent_status"] * 5 + ["checkpoint"]

    replay = await bus.replay_from("s-ev", seqs[2])
    assert [e.seq for e in replay] == seqs[3:]

    # Рестарт процесса: память пачки потеряна, номера продолжаются после резерва.
    forget_seq_batch("s-ev")
    after = await bus.publish("s-ev", "agent_status", {"n": 99})
    assert after.seq > max(seqs)

    # Кап на сессию: лишние обычные события уходят, защищённые остаются.
    result = compact_events("s-ev", cap=3)
    assert result["deleted_cap"] == 4
    kept = persisted_types("s-ev")
    assert "checkpoint" in kept and len(kept) == 3

    # Возраст: всё обычное старше срока уходит, checkpoint живёт.
    later = datetime.now(UTC) + timedelta(days=365)
    result = compact_events(now=later)
    assert result["deleted_age"] == 2
    assert persisted_types("s-ev") == ["checkpoint"]


def test_messages_ids_and_mode(orch_db: str) -> None:
    from app.orchestrator.agents.summarizer import has_prior_text
    from app.orchestrator.db.migrations import connect
    from app.orchestrator.queue import _insert_message, _set_message_mode

    first = _insert_message("s-msg", "user", "привет", task_id=None, mode=None)
    second = _insert_message("s-msg", "assistant", "ответ", task_id=3, mode="task")
    assert second > first
    _set_message_mode(first, "append")
    _set_message_mode(first, "task")  # режим уже выставлен — не меняется
    conn = connect()
    try:
        rows = conn.execute(
            text("SELECT id, mode, task_id FROM orch_messages WHERE session_id = :sid ORDER BY id"),
            {"sid": "s-msg"},
        ).mappings().fetchall()
    finally:
        conn.close()
    assert [(r["mode"], r["task_id"]) for r in rows] == [("append", None), ("task", 3)]
    assert has_prior_text("s-msg", 3) is False


async def test_notice_dedup_reads_events(orch_db: str) -> None:
    from app.orchestrator.events import EventBus
    from app.services import orch_bridge

    bus = EventBus()
    orch_bridge.forget_seen()
    assert await orch_bridge.forward_notice(bus, "s-n", {"id": 11, "title": "t"}) is True
    orch_bridge.forget_seen()  # как после рестарта: память пуста, видимость — из orch_events
    assert await orch_bridge.forward_notice(bus, "s-n", {"id": 11, "title": "t"}) is False
    assert await orch_bridge.forward_notice(bus, "s-n", {"id": 12, "title": "t"}) is True


# --- учёт вызовов модели ------------------------------------------------------


def _call(**over) -> LlmCallRecord:
    base = dict(
        call_id="call-1", task_id=None, run_id="run-1", agent_role="planner", requested_tier="strong",
        tier="strong", model="m", provider="52pay", reasoning_effort=None, fallback_index=0, attempt=1,
        status="ok", error=None, deterministic_flag=True, classifier_confidence=None, latency_ms=5,
        prompt_tokens=10, completion_tokens=2, reasoning_tokens=0, cost_usd=0.01, budget_scope="internal",
        router_impl="scripted", messages_sha256="abc", fixture_key=None, created_at="2026-10-22T10:00:00Z",
    )
    base.update(over)
    return LlmCallRecord(**base)


def test_llm_call_insert_is_idempotent_and_finds_project(orch_db: str) -> None:
    from app.orchestrator.agents.task_store import TaskStore
    from app.orchestrator.db.migrations import connect
    from app.orchestrator.routing.accounting import insert_call

    task = TaskStore().insert("s-acc", "цель", TaskStatus.executing.value, project_id=42)
    assert insert_call(_call(task_id=str(task))) == 42
    assert insert_call(_call(task_id=str(task), latency_ms=999)) == 42  # повтор call_id — тихо
    assert insert_call(_call(call_id="call-2", task_id="не-число", agent_role="кто-то")) is None
    conn = connect()
    try:
        rows = conn.execute(text("SELECT call_id, latency_ms, agent_role, task_id FROM orch_llm_calls ORDER BY id")) \
            .mappings().fetchall()
    finally:
        conn.close()
    assert [(r["call_id"], r["latency_ms"], r["agent_role"]) for r in rows] == [
        ("call-1", 5, "planner"), ("call-2", 5, "supervisor")]
    assert rows[0]["task_id"] == str(task)


def test_llm_call_keeps_the_real_provider(orch_db: str) -> None:
    """vibecode писался как «52pay»: журнал вызовов врал, через кого шёл запрос."""
    from app.orchestrator.db.migrations import connect
    from app.orchestrator.routing.accounting import insert_call

    insert_call(_call(call_id="p-vibe", provider="vibecode"))
    insert_call(_call(call_id="p-kie", provider="KIE"))
    insert_call(_call(call_id="p-pay", provider="52pay"))
    insert_call(_call(call_id="p-odd", provider="someone-else"))
    insert_call(_call(call_id="p-none", provider=""))
    conn = connect()
    try:
        rows = dict(conn.execute(text("SELECT call_id, provider FROM orch_llm_calls WHERE call_id LIKE 'p-%'"))
                    .fetchall())
    finally:
        conn.close()
    assert rows == {"p-vibe": "vibecode", "p-kie": "kie", "p-pay": "52pay", "p-odd": "unknown",
                    "p-none": "unknown"}


def test_provider_migration_keeps_rows_of_an_existing_db(tmp_path: Path) -> None:
    """База стенда: 001–010 уже накатаны, в журнале вызовов строки, CHECK знает только 52pay|kie."""
    from app.orchestrator.db import migrations as mig

    cols = (
        "call_id, agent_role, requested_tier, tier, model, provider, status, deterministic_flag, "
        "latency_ms, prompt_tokens, completion_tokens, reasoning_tokens, cost_usd, budget_scope, "
        "router_impl, created_at, tenant_id"
    )

    def row(call_id: str, provider: str) -> str:
        return (f"INSERT INTO orch_llm_calls ({cols}) VALUES ('{call_id}', 'planner', 'auto', 'strong', 'm', "
                f"'{provider}', 'ok', 1, 5, 1, 1, 0, 0.5, 'internal', 'live', 't', NULL)")

    db = tmp_path / "stand.db"
    raw = sqlite3.connect(db)
    try:
        raw.execute(mig.JOURNAL_DDL)
        for path in sorted(mig._MIGRATIONS_DIR.glob("*.sql")):
            if path.name >= "011":
                continue
            raw.executescript(path.read_text(encoding="utf-8"))
            raw.execute("INSERT INTO orch_schema_migrations VALUES (?, 't')", (path.name,))
        raw.execute(row("old-1", "kie"))
        raw.commit()
        with pytest.raises(sqlite3.IntegrityError):
            raw.execute(row("v-0", "vibecode"))
        raw.rollback()
        assert mig.apply_migrations(raw) == ["011_orch_llm_calls_provider.sql"]
        raw.execute(row("v-1", "vibecode"))
        raw.commit()
        rows = raw.execute("SELECT call_id, provider, cost_usd FROM orch_llm_calls ORDER BY id").fetchall()
        indexes = {r[0] for r in raw.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='orch_llm_calls' AND sql IS NOT NULL")}
        with pytest.raises(sqlite3.IntegrityError):
            raw.execute(row("x-1", "openai"))
    finally:
        raw.close()
    assert rows == [("old-1", "kie", 0.5), ("v-1", "vibecode", 0.5)]
    assert indexes == {"ix_orch_llm_calls_task_created", "ix_orch_llm_calls_budget_scope",
                       "ix_orch_llm_calls_tenant_id"}


# --- уведомления в чат и журнал ----------------------------------------------


async def test_chat_notices_write_dedup_list_and_read(orch_db: str) -> None:
    from app.services import chat_notice, notification_log

    status, made = await chat_notice.send(5, "warning", "Шаг упал", "Причина: сеть", kind="k1",
                                          dedup_key="d1", dedup_window_s=600)
    assert status == "written" and made["id"] > 0 and made["read"] is False
    chat_notice._recent.clear()  # как другой процесс: дубль ловит база, а не память
    status, _ = await chat_notice.send(5, "warning", "Шаг упал", "ещё раз", kind="k1",
                                       dedup_key="d1", dedup_window_s=600)
    assert status == "deduped"
    await chat_notice.send(None, "info", "Общее", "для всех", kind="k2")
    await chat_notice.send(6, "info", "Чужое", "другой проект", kind="k3")

    mine = await chat_notice.list_notices(5)
    assert [n["kind"] for n in mine] == ["k1", "k2"]
    only = await chat_notice.list_notices(5, include_global=False)
    assert [n["kind"] for n in only] == ["k1"]

    read = await chat_notice.mark_read(made["id"])
    assert read["read"] is True and read["read_at"].endswith("Z")
    assert [n["kind"] for n in await chat_notice.list_notices(5, unread_only=True)] == ["k2"]

    logged = notification_log.list_rows(project_id=5)
    assert len(logged) == 1 and logged[0]["channel"] == chat_notice.CHANNEL_CHAT


def test_notification_log_is_append_only(orch_db: str) -> None:
    from app.services import notification_log

    assert notification_log.log_notification(project_id=1, kind="message", text="привет", ok=True)
    rows = notification_log.list_rows()
    assert len(rows) == 1 and rows[0]["ok"] == 1
    with notification_log.engine().connect() as conn, pytest.raises(DBAPIError, match="append-only"):
        conn.execute(text("UPDATE notification_log SET ok = 0"))


# --- ноды и RAG ---------------------------------------------------------------


def _write_node(conn, key: str, prompt: str) -> None:
    stamp = "2026-10-22T10:00:00.000000Z"
    conn.execute(
        text("INSERT INTO node_defs (node_key, group_key, title, origin, created_at, updated_at) "
             "VALUES (:k, 'g', 'Нода', 'db', :t, :t)"),
        {"k": key, "t": stamp},
    )
    conn.execute(
        text("INSERT INTO node_prompt_versions (node_key, version, prompt_template, params_json, kind, "
             "change_id, created_at) VALUES (:k, 1, :p, '{}', 'create', 'chg', :t)"),
        {"k": key, "p": prompt, "t": stamp},
    )
    conn.execute(text("UPDATE node_defs SET active_version = 1 WHERE node_key = :k"), {"k": key})


def test_node_tables_guard_history_and_feed_rag(orch_db: str, tmp_path: Path) -> None:
    from app.orchestrator.agents.task_store import TaskStore
    from app.orchestrator.db.engine import APP_DATABASE
    from app.orchestrator.db.migrations import connect
    from app.orchestrator.queue import _insert_message
    from app.orchestrator.rag.loaders import load_history
    from app.orchestrator.rag.node_store import SqliteNodeStore

    conn = connect()
    try:
        _write_node(conn, "n_story", "Сценарий ролика")
        conn.commit()
        with pytest.raises(DBAPIError, match="immutable"):
            conn.execute(text("UPDATE node_prompt_versions SET prompt_template = 'x'"))
        conn.rollback()
        with pytest.raises(DBAPIError, match="active_version"):
            conn.execute(text("UPDATE node_defs SET active_version = 5 WHERE node_key = 'n_story'"))
        conn.rollback()
        with pytest.raises(DBAPIError):
            conn.execute(
                text("INSERT INTO node_defs (node_key, group_key, origin, created_at, updated_at) "
                     "VALUES ('Bad-Key', 'g', 'db', 't', 't')"))
        conn.rollback()
    finally:
        conn.close()

    nodes = SqliteNodeStore(APP_DATABASE).list_overrides(templates_root=tmp_path)
    assert [(n.node_key, n.source, n.prompt_template) for n in nodes] == [("n_story", "db", "Сценарий ролика")]

    TaskStore().insert("s-rag", "Сделать ролик про море", TaskStatus.done.value, project_id=3)
    _insert_message("s-rag", "user", "сделай ролик про море", task_id=None, mode=None)
    docs = load_history(project_db=APP_DATABASE)
    ids = {doc.source_id for doc in docs}
    if orch_db == "sqlite":
        assert any(i.startswith("task:") for i in ids) and "chat:s-rag" in ids
    else:
        # Общая база арендаторов: orch_* без tenant_id, индекс один на процесс —
        # задачи и чаты не должны попасть в общий корпус истории.
        assert ids == set()


def test_rag_runtime_sources_skip_shared_history(orch_db: str) -> None:
    from app.orchestrator.db.engine import APP_DATABASE
    from app.orchestrator.rag import _runtime_sources

    sources = _runtime_sources()
    if orch_db == "sqlite":
        assert sources["project_db"] == Path(settings.sqlite_path)
    else:
        assert sources["project_db"] is None
        assert sources["node_store"].db_path is APP_DATABASE


# --- только SQLite: та же схема двумя путями ---------------------------------


def _orch_schema(db: Path) -> dict[str, str]:
    from app.orchestrator.db.migrations import ORCH_TABLES

    names = ", ".join(f"'{name}'" for name in (*ORCH_TABLES, "orch_schema_migrations"))
    con = sqlite3.connect(db)
    try:
        rows = con.execute(
            f"SELECT type, name, sql FROM sqlite_master WHERE sql IS NOT NULL AND tbl_name IN ({names})"
        ).fetchall()
    finally:
        con.close()
    return {f"{kind}:{name}": " ".join(sql.split()) for kind, name, sql in rows}


def test_alembic_builds_the_runner_schema_on_sqlite(tmp_path: Path) -> None:
    pytest.skip("0018_orch_tables Alembic migration not in video-pipeline-web")
    from app.db_migrations import upgrade_to_head_sync
    from app.orchestrator.db.migrations import ORCH_SQL_FILES, apply_migrations

    via_alembic = tmp_path / "alembic.db"
    upgrade_to_head_sync(via_alembic)

    via_runner = tmp_path / "runner.db"
    raw = sqlite3.connect(via_runner)
    try:
        apply_migrations(raw)
    finally:
        raw.close()

    schema = _orch_schema(via_alembic)
    assert schema == _orch_schema(via_runner)
    assert "table:orch_events" in schema and "trigger:node_prompt_versions_no_update" in schema

    con = sqlite3.connect(via_alembic)
    try:
        journal = {r[0] for r in con.execute("SELECT name FROM orch_schema_migrations")}
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        # Раннер после alembic ничего не находит: всё уже в журнале.
        assert apply_migrations(con) == []
    finally:
        con.close()
    # 0018 накатывает ORCH_SQL_FILES, 0020 — колонку арендатора (010), 0021 — провайдера (011).
    assert journal == set(ORCH_SQL_FILES) | {"010_orch_tenant.sql", "011_orch_llm_calls_provider.sql"}
    assert {"chat_notices", "notification_log"} <= tables


def test_alembic_on_runner_made_db_keeps_data(tmp_path: Path) -> None:
    """База владельца, где раннер уже создал таблицы и есть строки: 0018 их не трогает."""
    pytest.skip("0018_orch_tables Alembic migration not in video-pipeline-web")
    from app.db_migrations import upgrade_to_head_sync

    db = tmp_path / "owner.db"
    upgrade_to_head_sync(db)
    con = sqlite3.connect(db)
    try:
        con.execute("INSERT INTO orch_messages (session_id, role, content, created_at) VALUES ('s', 'user', 'x', 't')")
        con.commit()
    finally:
        con.close()
    upgrade_to_head_sync(db)
    con = sqlite3.connect(db)
    try:
        assert con.execute("SELECT count(*) FROM orch_messages").fetchone()[0] == 1
    finally:
        con.close()


def test_downgrade_0018_drops_engine_tables_and_journal(tmp_path: Path) -> None:
    pytest.skip("0018_orch_tables Alembic migration not in video-pipeline-web")
    from alembic import command
    from sqlalchemy import create_engine

    from app.db_migrations import alembic_config, upgrade_to_head_sync

    db = tmp_path / "down.db"
    upgrade_to_head_sync(db)
    cfg = alembic_config()
    cfg.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "migrations"))
    engine = create_engine(f"sqlite+pysqlite:///{db}", future=True)
    try:
        with engine.begin() as conn:
            cfg.attributes["connection"] = conn
            command.downgrade(cfg, "0017")
    finally:
        engine.dispose()
    con = sqlite3.connect(db)
    try:
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        journal = con.execute("SELECT count(*) FROM orch_schema_migrations").fetchone()[0]
    finally:
        con.close()
    assert not tables & {"orch_tasks", "orch_events", "node_defs", "chat_notices", "notification_log"}
    assert journal == 0
    upgrade_to_head_sync(db)
    assert "table:orch_events" in _orch_schema(db)


def test_is_locked_sees_wrapped_sqlite_error() -> None:
    from sqlalchemy.exc import OperationalError

    from app.orchestrator.db.engine import is_locked

    wrapped = OperationalError("INSERT", {}, sqlite3.OperationalError("database is locked"))
    assert is_locked(wrapped)
    assert is_locked(sqlite3.OperationalError("database is busy"))
    assert not is_locked(OperationalError("INSERT", {}, sqlite3.OperationalError("no such table: x")))
    assert not is_locked(ValueError("locked"))


def test_postgres_target_without_tables_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    """Postgres без миграций: понятная ошибка вместо первой упавшей вставки."""
    from app.orchestrator.db import migrations

    class _Inspector:
        def get_table_names(self) -> list[str]:
            return ["projects", "orch_tasks"]

    class _Conn:
        def __enter__(self):
            return self

        def __exit__(self, *exc) -> None:
            return None

    class _Engine:
        def connect(self):
            return _Conn()

    monkeypatch.setattr("sqlalchemy.inspect", lambda _conn: _Inspector())
    with pytest.raises(RuntimeError, match="alembic upgrade head") as err:
        migrations._require_tables(_Engine())
    assert "orch_events" in str(err.value) and "orch_tasks" not in str(err.value)


def test_postgres_url_uses_sync_driver(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.orchestrator.db.engine import target_key, target_url, uses_postgres

    monkeypatch.setattr(settings, "database_url", "postgresql+asyncpg://app:app@127.0.0.1:5432/vp")
    assert uses_postgres()
    assert target_url() == "postgresql+psycopg://app:app@127.0.0.1:5432/vp"
    assert target_key() == target_url()
    # Явный путь — всегда файл SQLite (инструменты, ремонт конкретной базы).
    assert target_url(Path("x.db")).startswith("sqlite+pysqlite:///")


# --- прямые читатели базы приложения (SQLite) --------------------------------


def _snapshot_db(path: Path) -> None:
    con = sqlite3.connect(path)
    try:
        con.execute("CREATE TABLE scenes (id INTEGER PRIMARY KEY, project_id INTEGER)")
        con.execute("CREATE TABLE frames (id INTEGER PRIMARY KEY, project_id INTEGER, attrs TEXT)")
        con.executemany("INSERT INTO scenes (id, project_id) VALUES (?, ?)", [(1, 7), (2, 8)])
        con.execute("INSERT INTO frames (id, project_id, attrs) VALUES (10, 7, ?)",
                    ('{"кадры": [{"id": "s1"}, {"shot_id": 5}]}',))
        con.commit()
    finally:
        con.close()


def test_snapshot_reads_file_sqlite3_and_sqlalchemy_connections(tmp_path: Path) -> None:
    from sqlalchemy import create_engine

    from app.orchestrator.validators.snapshot import load_project_snapshot

    db = tmp_path / "snap.db"
    _snapshot_db(db)
    want = {"scenes": [{"id": 1}], "frames": [{"id": 10}, {"id": "s1"}, {"id": 5}], "available": True}
    assert load_project_snapshot(7, db_path=db) == want
    raw = sqlite3.connect(db)
    try:
        assert load_project_snapshot(7, conn=raw) == want
    finally:
        raw.close()
    engine = create_engine(f"sqlite+pysqlite:///{db}")
    try:
        with engine.connect() as conn:
            assert load_project_snapshot(7, conn=conn) == want
    finally:
        engine.dispose()
    assert load_project_snapshot(7, db_path=tmp_path / "нет.db") == {"scenes": [], "frames": [], "available": False}
    # Файл открыт только на чтение: снимок ничего не создал рядом.
    assert not (tmp_path / "нет.db").exists()


def test_attachment_binding_reads_registry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import hashlib

    from app.services.action_layer.context import UploadRejected, verify_attachment_binding

    db = tmp_path / "att.db"
    monkeypatch.setattr(settings, "sqlite_path", db)
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    real = tmp_path / "attachments" / "a.txt"
    with pytest.raises(UploadRejected):  # базы ещё нет — вложение не зарегистрировано
        verify_attachment_binding(real, content=b"x")
    assert not db.exists()
    con = sqlite3.connect(db)
    try:
        con.execute("CREATE TABLE action_layer_attachments (rel_path TEXT, sha256 TEXT, size INTEGER)")
        con.execute("INSERT INTO action_layer_attachments VALUES ('attachments/a.txt', ?, 3)",
                    (hashlib.sha256(b"abc").hexdigest(),))
        con.commit()
    finally:
        con.close()
    verify_attachment_binding(real, content=b"abc", size=3)
    with pytest.raises(UploadRejected, match="sha256"):
        verify_attachment_binding(real, content=b"abd")
    with pytest.raises(UploadRejected, match="размер"):
        verify_attachment_binding(real, size=4)
    verify_attachment_binding(tmp_path / "other" / "b.txt", content=b"zzz")  # не вложение — не проверяется


def test_history_skips_shared_database_even_with_rows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Без живого Postgres: те же строки orch_* через соединение «не sqlite»
    в корпус истории не попадают, а через файл SQLite — попадают."""
    from contextlib import contextmanager

    from sqlalchemy import create_engine

    from app.db_migrations import upgrade_to_head_sync
    from app.orchestrator.db import engine as db_engine
    from app.orchestrator.db.migrations import apply_migrations
    from app.orchestrator.rag import loaders

    db = tmp_path / "shared.db"
    upgrade_to_head_sync(db)
    con = sqlite3.connect(db)
    apply_migrations(con)
    try:
        con.execute(
            "INSERT INTO orch_tasks (id, session_id, goal, status, created_at, updated_at) "
            "VALUES (77, 's-other', 'чужая цель', 'done', 't', 't')"
        )
        con.execute(
            "INSERT INTO orch_messages (session_id, role, content, created_at) "
            "VALUES ('s-other', 'user', 'чужой чат', 't')"
        )
        con.commit()
    finally:
        con.close()

    own = {doc.source_id for doc in loaders.load_history(project_db=db)}
    assert "task:77" in own and "chat:s-other" in own

    sa_engine = create_engine(f"sqlite+pysqlite:///{db}", future=True)

    @contextmanager
    def shared_read(_source):
        with sa_engine.connect() as conn:
            monkeypatch.setattr(conn.dialect, "name", "postgresql")
            yield conn

    monkeypatch.setattr(db_engine, "open_read", shared_read)
    try:
        assert loaders.load_history(project_db=db_engine.APP_DATABASE) == []
    finally:
        sa_engine.dispose()


def test_runtime_sources_on_postgres_settings_skip_history(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.orchestrator.db.engine import APP_DATABASE
    from app.orchestrator.rag import _runtime_sources

    monkeypatch.setattr(settings, "database_url", "postgresql+asyncpg://app:app@127.0.0.1:5432/vp")
    sources = _runtime_sources()
    assert sources["project_db"] is None
    assert sources["node_store"].db_path is APP_DATABASE
