"""Чат оркестратора для каждого клиента студии: изоляция на живом Postgres.

Чат открыт роли `member`, значит его история, планы, одобрения, журнал
действий и снимки для отката — данные арендатора (ревизия 0020). Здесь два
клиента, A и B, и проверяется то, что можно проверить только сервером:

* строки движка, записанные от имени A — сырым SQL (`orch_*`) и ORM (слой
  действий), — несут арендатора A, и B их не видит: список пуст, чтение,
  одобрение, запуск и откат — 404;
* фоновое выполнение плана A (задача, порождённая запросом) пишет строки A;
* проход после рестарта идёт как воркер: владелец, затем каждый арендатор;
* касса провайдеров общая на студию, политика её не режет;
* каждая таблица живой базы учтена, политики включены с FORCE;
* инструменты студии (реестр резервов) — 403 участнику, админу 200.

Подать Postgres так же, как для `tests/test_rls_postgres.py`: роль в URL —
обычная, не суперпользователь. Пропускается без ``TEST_DATABASE_URL``.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from tests.orch_pg_support import TEST_DB_URL

pytestmark = pytest.mark.skipif(not TEST_DB_URL, reason="TEST_DATABASE_URL не задан — живого Postgres нет")

ENGINE_MODULES = (
    "app.services.action_layer.store",
    "app.services.action_layer.attachments",
    "app.services.actions_recovery",
    "app.services.actions_v1",
    "app.services.change_control.store",
    "app.services.decision_config",
    "app.services.decision_state",
    "app.services.diagnosis",
    "app.services.kill_switch",
    "app.services.managed_launch",
    "app.services.media_jobs",
    "app.services.observation",
    "app.services.outsee_idempotency",
    "app.services.profiles_state",
    "app.services.spend_guard",
)


@pytest_asyncio.fixture
async def pg(monkeypatch):
    """Приложение в режиме учётных записей на живом Postgres, два клиента и админ."""
    import importlib

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    import app.db as db_mod
    from app.orchestrator.agents.runtime import build_runtime, set_runtime
    from app.settings import settings
    from app.web.api import create_app
    from app.web.deps import get_session
    from tests import accounts_harness as ah
    from tests.orch_pg_support import prepare_postgres, reset_engine_state

    for name in ENGINE_MODULES:
        importlib.import_module(name)
    monkeypatch.setattr(settings, "database_url", TEST_DB_URL)
    ah.configure(monkeypatch, allow_sqlite=False)
    reset_engine_state()
    prepare_postgres()

    engine = create_async_engine(TEST_DB_URL, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    ah.bind_identity_session(monkeypatch, factory)

    async def _gen():
        async with factory() as s:
            yield s

    mark = uuid.uuid4().hex[:8]
    a = await ah.make_account(factory, email=f"orch-a-{mark}@studio.local")
    b = await ah.make_account(factory, email=f"orch-b-{mark}@studio.local")
    admin = await ah.make_admin(factory, email=f"orch-admin-{mark}@studio.local")

    runtime = build_runtime()
    set_runtime(runtime)
    app = create_app()
    app.dependency_overrides[get_session] = _gen
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield {
                "client": client,
                "app": app,
                "factory": factory,
                "runtime": runtime,
                "a": a,
                "b": b,
                "admin": admin,
                "db_mod": db_mod,
            }
        await runtime.queue.drain(timeout=5.0)
    finally:
        set_runtime(None)
        reset_engine_state()
        await engine.dispose()


async def _scalar_as(factory, tenant: str | None, sql: str, **params):
    """Один запрос от имени арендатора — как его видит RLS."""
    from app.services.tenant import tenant_scope

    with tenant_scope(tenant):
        async with factory() as session:
            return (await session.execute(text(sql), params)).scalar()


# ── схема ───────────────────────────────────────────────────────────────────


async def test_every_table_is_classified_and_forced(pg) -> None:
    from sqlalchemy import inspect

    from app.orchestrator.db.engine import get_engine
    from app.services.rls_check import check_rls
    from app.services.tenant_tables import check_coverage, check_database_coverage

    assert check_coverage() == []
    with get_engine().connect() as conn:
        names = inspect(conn).get_table_names()
    assert check_database_coverage(names) == []

    async with pg["factory"]() as session:
        report = await check_rls(session)
    assert not report.superuser and not report.bypassrls, "роль теста обходит RLS — проверка бессмысленна"
    assert report.unprotected == [] and report.unforced == [] and report.missing_policy == [], (
        report.problems()
    )

    for table in ("orch_tasks", "orch_events", "action_layer_plans", "action_layer_snapshots"):
        forced = await _scalar_as(
            pg["factory"], None, "select relforcerowsecurity from pg_class where relname = :t", t=table
        )
        assert forced is True, table


# ── чат: сырой SQL движка ───────────────────────────────────────────────────


async def test_chat_rows_belong_to_their_tenant(pg) -> None:
    """Сырые вставки `orch_*` получают арендатора транзакции, чужое не видно."""
    from app.orchestrator.agents.task_store import TaskStore
    from app.orchestrator.contracts import TaskStatus
    from app.services.orch_tenancy import scoped_session
    from app.services.tenant import tenant_scope

    a, b = pg["a"].user_id, pg["b"].user_id
    store = TaskStore()
    with tenant_scope(a):
        task_a = await asyncio.to_thread(
            store.insert, scoped_session("chat", a), "цель A", TaskStatus.waiting_approval.value
        )
    with tenant_scope(b):
        task_b = await asyncio.to_thread(
            store.insert, scoped_session("chat", b), "цель B", TaskStatus.waiting_approval.value
        )
        assert await asyncio.to_thread(store.get, task_a) is None
        assert [row["id"] for row in await asyncio.to_thread(store.list_open)] == [task_b]
    with tenant_scope(a):
        row = await asyncio.to_thread(store.get, task_a)
    assert row is not None and str(row["tenant_id"]) == a

    # Через HTTP: чужая задача той же внешней сессии — 404, своя — 200.
    client = pg["client"]
    path = f"/api/orchestrator/chat/chat/partial-changes?task_id={task_a}"
    assert (await client.get(path, headers=pg["b"].auth)).status_code == 404
    assert (await client.get(path, headers=pg["a"].auth)).status_code == 200


async def test_chat_message_and_events_are_tenant_scoped(pg) -> None:
    """Реплика A уходит в шину сессии A; B с тем же именем чата её не видит."""
    from app.services.orch_tenancy import scoped_session

    client, factory = pg["client"], pg["factory"]
    a, b = pg["a"].user_id, pg["b"].user_id
    res = await client.post(
        "/api/orchestrator/chat/main/message", json={"text": "привет"}, headers=pg["a"].auth
    )
    assert res.status_code == 200, res.text
    assert res.json()["session"] == "main"
    await pg["runtime"].queue.drain(timeout=5.0)

    internal_a = scoped_session("main", a)
    sql = "select count(*) from orch_events where session_id = :s"
    assert await _scalar_as(factory, a, sql, s=internal_a) >= 1
    assert await _scalar_as(factory, b, sql, s=internal_a) == 0
    assert await _scalar_as(factory, None, sql, s=internal_a) == 0, (
        "владелец видит только строки без арендатора"
    )
    stamped = await _scalar_as(
        factory,
        a,
        "select count(*) from orch_events where session_id = :s and tenant_id = :t",
        s=internal_a,
        t=a,
    )
    assert stamped >= 1

    status_b = await client.get("/api/orchestrator/chat/main/status", headers=pg["b"].auth)
    assert status_b.status_code == 200
    assert status_b.json() == {"session": "main", "task_id": None, "status": "", "resume_allowed": False}


async def test_foreign_project_cannot_be_streamed_or_bound(pg) -> None:
    """Номер чужого проекта в чате — 404, а не подписка на его уведомления."""
    from app.models import Project, ProjectStatus
    from app.services.tenant import tenant_scope

    a = pg["a"].user_id
    with tenant_scope(a):
        async with pg["factory"]() as session:
            project = Project(
                slug=f"orch-a-{uuid.uuid4().hex[:8]}",
                topic="чат",
                status=ProjectStatus.new,
                hero_mode="no_hero",
            )
            session.add(project)
            await session.commit()
            project_id = project.id
    client = pg["client"]
    res = await client.get(f"/api/orchestrator/chat/s/stream?project_id={project_id}", headers=pg["b"].auth)
    assert res.status_code == 404
    res = await client.post(
        "/api/orchestrator/chat/s/message", json={"text": "x", "project_id": project_id}, headers=pg["b"].auth
    )
    assert res.status_code == 404


async def test_restart_settle_runs_for_every_tenant(pg) -> None:
    """Проход после рестарта — как воркер: владелец и каждый арендатор под своим именем."""
    from app.orchestrator.agents.task_store import TaskStore
    from app.orchestrator.contracts import TaskStatus
    from app.services.orch_tenancy import for_each_tenant, scoped_session
    from app.services.tenant import tenant_scope

    a, b = pg["a"].user_id, pg["b"].user_id
    store = TaskStore()
    ids: dict[str | None, int] = {}
    for tenant in (None, a, b):
        with tenant_scope(tenant):
            ids[tenant] = await asyncio.to_thread(
                store.insert, scoped_session("restart", tenant), "прервано", TaskStatus.executing.value
            )

    closed = await for_each_tenant(pg["runtime"].supervisor.settle_after_restart, what="test settle")
    assert set(ids.values()) <= set(closed)
    for tenant, task_id in ids.items():
        with tenant_scope(tenant):
            row = await asyncio.to_thread(store.get, task_id)
        assert row is not None and row["status"] == TaskStatus.failed.value
        stamp = None if row["tenant_id"] is None else str(row["tenant_id"])
        assert stamp == tenant
    # Событие о закрытии задачи A записано от имени A.
    sql = "select count(*) from orch_events where task_id = :i and type = 'task_failed'"
    assert await _scalar_as(pg["factory"], a, sql, i=ids[a]) == 1
    assert await _scalar_as(pg["factory"], b, sql, i=ids[a]) == 0


# ── слой действий: планы, одобрения, фоновое выполнение, снимки ─────────────


def _snapshot_spec(action_id: str):
    """Разрушающий шаг (нужно одобрение): снимок «до» и внутренний вызов API."""
    from app.services.action_layer import store
    from app.services.action_layer.spec import ActionSpec

    async def _run(ctx, params):
        me = await ctx.call("GET", "/me")
        plan = await ctx.call("GET", f"/action-layer/plans/{ctx.plan_id}")
        snap = await store.save_snapshot(
            plan_id=ctx.plan_id or "",
            step_index=ctx.step_index,
            action_id=action_id,
            project_id=None,
            kind="test",
            target={"what": "проверка"},
            before={"value": 1},
            after={"value": 2},
            restorable=True,
            actor=ctx.operator,
        )
        return {"email": me.get("email"), "plan_seen": plan.get("id") == ctx.plan_id, "snapshot_id": snap}

    return ActionSpec(
        id=action_id,
        title_ru="Проверка арендатора",
        description_ru="Пишет снимок и зовёт API изнутри",
        params_schema={"type": "object", "properties": {}, "additionalProperties": False},
        executor=_run,
        risk="destructive",
        destructive=True,
        destructive_effects=lambda p: "проверочная запись",
    )


async def test_plan_approval_run_and_snapshot_are_tenant_scoped(pg) -> None:
    from app.services.action_layer import executor, store
    from app.services.action_layer.spec import ensure_builtin_actions, registry
    from app.services.tenant import tenant_scope

    ensure_builtin_actions()
    action_id = "t.tenant_snapshot"
    registry.register(_snapshot_spec(action_id))
    client, factory = pg["client"], pg["factory"]
    auth_a, auth_b = pg["a"].auth, pg["b"].auth
    a = pg["a"].user_id
    try:
        res = await client.post(
            "/api/action-layer/plans/preview",
            json={"steps": [{"action_id": action_id, "params": {}}]},
            headers=auth_a,
        )
        assert res.status_code == 200, res.text
        plan_id = res.json()["plan_id"]
        assert res.json()["preview"]["needs_approval"] is True

        # B: плана A не существует — ни в списке, ни поштучно, ни для действий.
        listed = await client.get("/api/action-layer/plans", headers=auth_b)
        assert plan_id not in [p["id"] for p in listed.json()["items"]]
        assert (await client.get(f"/api/action-layer/plans/{plan_id}", headers=auth_b)).status_code == 404
        assert (
            await client.post(f"/api/action-layer/plans/{plan_id}/approve", headers=auth_b)
        ).status_code == 404
        assert (await client.post(f"/api/action-layer/plans/{plan_id}/run", headers=auth_b)).status_code in (
            403,
            404,
        )
        assert (
            await client.post(f"/api/action-layer/plans/{plan_id}/stop", headers=auth_b)
        ).status_code == 404
        history_b = await client.get(f"/api/action-layer/history?plan_id={plan_id}", headers=auth_b)
        assert history_b.status_code == 200 and history_b.json()["items"] == []

        # A одобряет и запускает: шаг идёт фоновой задачей, порождённой запросом.
        ok = await client.post(f"/api/action-layer/plans/{plan_id}/approve", headers=auth_a)
        assert ok.status_code == 200, ok.text
        run = await client.post(f"/api/action-layer/plans/{plan_id}/run", headers=auth_a)
        assert run.status_code == 200, run.text
        await executor.wait(plan_id)
        view = (await client.get(f"/api/action-layer/plans/{plan_id}", headers=auth_a)).json()
        assert view["status"] == "succeeded", view
        result = view["steps"][0]["result"]
        assert result["email"] == pg["a"].email
        assert result["plan_seen"] is True, "внутренний вызов шёл не от имени A"
        snapshot_id = int(result["snapshot_id"])
    finally:
        registry.unregister(action_id)

    # Все строки плана записаны от имени A — и запросом, и фоновой задачей.
    for table, column in (
        ("action_layer_plans", "id"),
        ("action_layer_steps", "plan_id"),
        ("action_layer_approvals", "plan_id"),
        ("action_layer_history", "plan_id"),
        ("action_layer_snapshots", "plan_id"),
    ):
        sql = f"select count(*) from {table} where {column} = :p and tenant_id = :t"  # noqa: S608
        assert await _scalar_as(factory, a, sql, p=plan_id, t=a) >= 1, table
        total = f"select count(*) from {table} where {column} = :p"  # noqa: S608
        assert await _scalar_as(factory, pg["b"].user_id, total, p=plan_id) == 0, table

    # Откат: снимок для B не существует, для A — на месте.
    with tenant_scope(pg["b"].user_id):
        assert await store.get_snapshot(snapshot_id) is None
    with tenant_scope(a):
        assert await store.get_snapshot(snapshot_id) is not None


async def test_same_idempotency_key_in_two_tenants(pg) -> None:
    """Ключ повтора общий на базу, строки под RLS: два клиента не мешают друг другу."""
    client = pg["client"]
    body = {"steps": [{"action_id": "projects.overview", "params": {}}], "request_text": "одинаковый"}
    key = f"same-key-{uuid.uuid4().hex[:8]}"
    plan_ids = []
    for account in (pg["a"], pg["b"]):
        res = await client.post(
            "/api/action-layer/plans/preview",
            json=body,
            headers={**account.auth, "Idempotency-Key": key},
        )
        assert res.status_code == 200, res.text
        assert "plan_id" in res.json(), f"B получил чужой ответ по ключу повтора: {res.text[:200]}"
        plan_ids.append(res.json()["plan_id"])
    assert plan_ids[0] != plan_ids[1]
    # Повтор у A — его же ответ, а не новый план.
    again = await client.post(
        "/api/action-layer/plans/preview", json=body, headers={**pg["a"].auth, "Idempotency-Key": key}
    )
    assert again.status_code == 200 and again.json()["plan_id"] == plan_ids[0]


async def test_macro_id_taken_by_another_tenant_is_a_clean_refusal(pg) -> None:
    from app.services.action_layer import macros
    from app.services.tenant import tenant_scope

    macro_id = f"user.shared_{uuid.uuid4().hex[:6]}"
    steps = [{"action_id": "project.status", "params": {"project_id": "{{project_id}}"}}]
    schema = {
        "type": "object",
        "properties": {"project_id": {"type": "integer", "minimum": 1}},
        "required": ["project_id"],
        "additionalProperties": False,
    }
    with tenant_scope(pg["a"].user_id):
        saved = await macros.save_macro(
            "op-a",
            title_ru="Макрос A",
            description_ru="",
            steps=steps,
            params_schema=schema,
            macro_id=macro_id,
        )
    assert saved.id == macro_id
    with tenant_scope(pg["b"].user_id):
        with pytest.raises(macros.MacroError):
            await macros.save_macro(
                "op-b",
                title_ru="Макрос B",
                description_ru="",
                steps=steps,
                params_schema=schema,
                macro_id=macro_id,
            )
        with pytest.raises(LookupError):
            await macros.get_macro(macro_id, "op-b")


# ── общее на студию ─────────────────────────────────────────────────────────


async def test_provider_spend_ledger_stays_shared(pg) -> None:
    """Дневной лимит считается по всей студии: трата A видна в сумме у B."""
    from app.services import spend_guard
    from app.services.tenant import tenant_scope

    marker = 900_000 + int(uuid.uuid4().int % 99_999)
    with tenant_scope(pg["a"].user_id):
        async with pg["factory"]() as session:
            await spend_guard.ensure_schema(session)
            session.add(
                spend_guard.SpendLedger(
                    day=spend_guard._today(), kind="actual", project_id=marker, usd=1.25, source="test"
                )
            )
            await session.commit()
    for tenant in (pg["b"].user_id, None):
        with tenant_scope(tenant):
            async with pg["factory"]() as session:
                totals = await spend_guard.totals(session, project_id=marker)
        assert totals["project_spent"] == pytest.approx(1.25)
    rls = await _scalar_as(
        pg["factory"], None, "select relrowsecurity from pg_class where relname = 'spend_ledger'"
    )
    assert rls is False


async def test_studio_tools_are_admin_only(pg) -> None:
    client = pg["client"]
    assert (await client.get("/api/action-layer/reserves", headers=pg["a"].auth)).status_code == 403
    assert (await client.get("/api/action-layer/reserves", headers=pg["admin"].auth)).status_code == 200
    assert (await client.get("/api/action-layer/catalog", headers=pg["a"].auth)).status_code == 200
