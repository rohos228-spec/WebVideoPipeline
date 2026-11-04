"""Новые межпроектные возможности чата (перенос int/chat-v1 fx/queue_stop, phrases5, phrases6)
на живом Postgres с двумя арендаторами: каждый видит и трогает только своё.

* правка модели «во всех проектах» — список проектов планировщику только свои;
* аварийный стоп чата снимает с общей очереди генерации только свои проекты;
  метка «номер ставил чат» — сессия и арендатор: чужую сессию ручка очереди не ставит;
* отчёт по проектам — чужой проект «не найден», без его названия и цифр;
* факт «очередь генерации» в ответе на вопрос — число и номера только своих проектов.

Postgres подаётся как для `tests/test_orch_tenant_postgres.py` (``TEST_DATABASE_URL``,
обычная роль). Пропускается без ``TEST_DATABASE_URL``.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest

from tests.orch_pg_support import TEST_DB_URL
from tests.test_orch_tenant_postgres import pg  # noqa: F401  фикстура

pytestmark = pytest.mark.skipif(not TEST_DB_URL, reason="TEST_DATABASE_URL не задан — живого Postgres нет")


async def _project_of(pg, tenant: str, *, title: str, meta: dict | None = None) -> int:  # noqa: F811
    from app.models import Project, ProjectStatus
    from app.services.tenant import tenant_scope

    with tenant_scope(tenant):
        async with pg["factory"]() as s:
            p = Project(
                slug=f"xp-{uuid.uuid4().hex[:8]}",
                title=title,
                topic="t",
                status=ProjectStatus.new,
                hero_mode="no_hero",
                auto_mode=True,
                meta=dict(meta or {}),
            )
            s.add(p)
            await s.commit()
            return p.id


async def _meta_as_owner(pg, pid: int) -> dict:  # noqa: F811
    """meta проекта как она лежит в базе (читаем от имени владельца строки)."""
    from app.models import Project
    from app.services.tenant import tenant_scope

    for tenant in (pg["a"].user_id, pg["b"].user_id):
        with tenant_scope(tenant):
            async with pg["factory"]() as s:
                row = await s.get(Project, pid)
                if row is not None:
                    return dict(row.meta or {})
    raise AssertionError(f"проект #{pid} не найден ни у одного арендатора")


def _queue_file(monkeypatch, ids: list[int]) -> list[int]:
    """Общий файл очереди генерации — в памяти теста."""
    queue = list(ids)

    def _get() -> list[int]:
        return list(queue)

    def _set(new: list[int]) -> list[int]:
        queue[:] = [int(x) for x in new]
        return list(queue)

    monkeypatch.setattr("app.services.gen_queue.get_gen_queue", _get)
    monkeypatch.setattr("app.services.sidebar_layout.get_gen_queue", _get)

    def _toggle(pid: int) -> list[int]:
        if int(pid) in queue:
            queue.remove(int(pid))
        else:
            queue.append(int(pid))
        return list(queue)

    monkeypatch.setattr("app.services.sidebar_layout.set_gen_queue", _set)
    monkeypatch.setattr("app.services.sidebar_layout.toggle_gen_queue", _toggle)
    monkeypatch.setattr("app.services.sidebar_layout.clear_gen_queue_halted", lambda **_k: False)
    monkeypatch.setattr("app.services.gen_queue.is_gen_queue_halted", lambda: False)
    return queue


# ── правка модели во всех проектах ────────────────────────────────────────────


async def test_mass_model_edit_lists_only_own_projects(pg) -> None:  # noqa: F811
    from app.orchestrator.agents.action_catalog import ActionCatalog, _studio_projects
    from app.orchestrator.contracts import TaskStatus
    from app.services.orch_tenancy import own_projects, scoped_session
    from app.services.tenant import tenant_scope

    a, b = pg["a"].user_id, pg["b"].user_id
    pid_a = await _project_of(pg, a, title="Лисы клиента А")
    pid_b = await _project_of(pg, b, title="Совы клиента Б")
    sup = pg["runtime"].supervisor

    assert [pid for pid, _t in await asyncio.to_thread(own_projects, a)] == [pid_a]
    assert [pid for pid, _t in await asyncio.to_thread(own_projects, b)] == [pid_b]
    # Номера чужого арендатора отбрасываются и при явном списке (так читает очередь).
    assert await asyncio.to_thread(own_projects, a, [pid_b, pid_a]) == [(pid_a, "Лисы клиента А")]
    # Владелец (арендатора нет) клиентских проектов не видит.
    assert await asyncio.to_thread(own_projects, None) == []

    with tenant_scope(b):
        tid = await asyncio.to_thread(
            sup.tasks.insert, scoped_session("mass", b), "g", TaskStatus.executing.value
        )
        listed = await asyncio.to_thread(_studio_projects, tid)
        block = await asyncio.to_thread(
            ActionCatalog(project_of=lambda _t: None).block,
            "во всех проектах поменяй модель картинок на дефолтную",
            tid,
        )
    assert listed == [(pid_b, "Совы клиента Б")]
    assert f"{pid_b} «Совы клиента Б»" in block
    assert "Лисы клиента А" not in block and f"{pid_a} «" not in block


# ── аварийный стоп чата и очередь генерации ──────────────────────────────────


async def test_chat_kill_dequeues_only_own_tenant_projects(pg, monkeypatch) -> None:  # noqa: F811
    from app.orchestrator.agents import supervisor as sup_mod
    from app.orchestrator.contracts import PlanStep, TaskStatus
    from app.services.gen_queue_run import CHAT_OWNER_KEY
    from app.services.orch_tenancy import scoped_session
    from app.services.tenant import tenant_scope

    a, b = pg["a"].user_id, pg["b"].user_id
    sess_a, sess_b = scoped_session("chat", a), scoped_session("chat", b)
    # У проекта А метка поддельная: будто его ставила сессия Б. RLS всё равно не даёт его снять.
    pid_a = await _project_of(pg, a, title="A", meta={"gen_queue_run": {CHAT_OWNER_KEY: sess_b}})
    pid_a2 = await _project_of(pg, a, title="A2", meta={"gen_queue_run": {CHAT_OWNER_KEY: sess_a}})
    pid_b = await _project_of(pg, b, title="B", meta={"gen_queue_run": {CHAT_OWNER_KEY: sess_b}})
    queue = _queue_file(monkeypatch, [pid_a, pid_a2, pid_b])
    monkeypatch.setattr(sup_mod, "QUEUE_RECHECK_DELAY_SEC", 0.0)
    sup = pg["runtime"].supervisor

    with tenant_scope(b):
        tid = await asyncio.to_thread(sup.tasks.insert, sess_b, "g", TaskStatus.done.value)
        step = PlanStep(
            action="queue.enqueue_many",
            params={"project_ids": [pid_a, pid_a2, pid_b]},
            expected="x",
            mutating=True,
        )
        await asyncio.to_thread(sup.tasks.insert_step, tid, 0, step)
    # Кандидаты читаются от имени арендатора сессии, даже если kill пришёл без контекста.
    with tenant_scope(None):
        assert sup._session_queue_projects(sess_b) == [pid_a, pid_a2, pid_b]
        assert sup._session_queue_projects(sess_a) == []
        got = await sup._dequeue_session_queue(sess_b, tid)

    assert got["removed"] == [pid_b], got
    assert queue == [pid_a, pid_a2]
    meta_a, meta_a2, meta_b = [await _meta_as_owner(pg, p) for p in (pid_a, pid_a2, pid_b)]
    assert not meta_a.get("user_stop") and not meta_a2.get("user_stop")
    assert meta_a["gen_queue_run"][CHAT_OWNER_KEY] == sess_b  # чужое не тронуто вовсе
    assert meta_b.get("user_stop") and CHAT_OWNER_KEY not in (meta_b.get("gen_queue_run") or {})

    # Прямой вызов снятия от имени сессии Б с номером проекта А: проект не виден — не снимается.
    queue[:] = [pid_a, pid_a2]
    left = await sup_mod._dequeue_chat_from_queue([pid_a], sess_b)
    assert left["removed"] == [] and left["kept_user"] == [] and queue == [pid_a, pid_a2]
    # Своя сессия А снимает свой номер.
    mine = await sup_mod._dequeue_chat_from_queue([pid_a, pid_a2], sess_a)
    assert mine["removed"] == [pid_a2] and mine["kept_user"] == [pid_a] and queue == [pid_a]
    # Повторная проверка после стопа тоже под арендатором сессии: чужой проект не трогает.
    assert await sup_mod._recheck_dequeued_in_db([pid_a], sess_b, 0.0) == []
    assert not (await _meta_as_owner(pg, pid_a)).get("user_stop")


async def test_enqueue_route_ignores_chat_session_of_another_tenant(pg, monkeypatch) -> None:  # noqa: F811
    from app.services.gen_queue_run import CHAT_OWNER_KEY
    from app.services.orch_tenancy import scoped_session

    a, b = pg["a"].user_id, pg["b"].user_id
    queue = _queue_file(monkeypatch, [])
    pid_a = await _project_of(pg, a, title="A")
    pid_a2 = await _project_of(pg, a, title="A2")
    client, auth = pg["client"], pg["a"].auth

    res = await client.post(
        "/api/sidebar-layout/gen-queue/enqueue",
        json={"project_id": pid_a, "by_chat": scoped_session("chat", b)},
        headers=auth,
    )
    assert res.status_code == 200, res.text
    assert CHAT_OWNER_KEY not in ((await _meta_as_owner(pg, pid_a)).get("gen_queue_run") or {})

    res = await client.post(
        "/api/sidebar-layout/gen-queue/enqueue",
        json={"project_id": pid_a2, "by_chat": scoped_session("chat", a)},
        headers=auth,
    )
    assert res.status_code == 200, res.text
    owner = ((await _meta_as_owner(pg, pid_a2)).get("gen_queue_run") or {}).get(CHAT_OWNER_KEY)
    assert owner == scoped_session("chat", a)
    assert queue == [pid_a, pid_a2]

    # Чужой проект арендатор в очередь не поставит.
    pid_b = await _project_of(pg, b, title="B")
    res = await client.post("/api/sidebar-layout/gen-queue/enqueue", json={"project_id": pid_b}, headers=auth)
    assert res.status_code == 404, res.text
    assert pid_b not in queue


# ── отчёт по проектам ─────────────────────────────────────────────────────────


class _Ctx:
    """Внутренние вызовы отчёта: деталей и прогонов нод нет (отчёт берёт строку проекта из базы)."""

    operator = "test"
    plan_id = None
    project_id = None

    async def call(self, method, path, **_kw):
        return [] if "node-runs" in path or path.endswith("/runs") else {}


async def test_report_projects_shows_only_own_projects(pg) -> None:  # noqa: F811
    from app.services.action_layer import report_gpt as rg
    from app.services.tenant import tenant_scope

    a, b = pg["a"].user_id, pg["b"].user_id
    pid_a = await _project_of(pg, a, title="Свой ролик А")
    pid_b = await _project_of(pg, b, title="Секретный ролик Б")
    with tenant_scope(a):
        out = await rg.report_projects(_Ctx(), {"project_ids": [pid_a, pid_b], "kind": "json"})
        every = await rg.report_projects(_Ctx(), {"kind": "json"})
    rows = {r["project_id"]: r for r in out["rows"]}
    assert rows[pid_a]["found"] and rows[pid_a]["title"] == "Свой ролик А"
    assert not rows[pid_b]["found"] and rows[pid_b]["title"] == "проект не найден"
    assert out["summary"]["missing"] == [pid_b]
    assert "Секретный" not in str(out)
    # Без списка — последние проекты арендатора, чужих в нём нет.
    assert {r["project_id"] for r in every["rows"]} == {pid_a}


# ── факт «очередь генерации» в вопросах ──────────────────────────────────────


async def test_queue_capacity_fact_lists_only_own_projects(pg, monkeypatch) -> None:  # noqa: F811
    from app.orchestrator.agents.question_context import QuestionContext, queue_capacity_text
    from app.services.orch_tenancy import scoped_session
    from app.services.tenant import tenant_scope

    a, b = pg["a"].user_id, pg["b"].user_id
    pid_a = await _project_of(pg, a, title="A")
    pid_b = await _project_of(pg, b, title="B")
    pid_b2 = await _project_of(pg, b, title="B2")
    _queue_file(monkeypatch, [pid_b, pid_a, pid_b2])
    monkeypatch.setattr("app.services.gen_queue.worker_max_parallel", lambda: 2)

    with tenant_scope(a):
        text_a = await asyncio.to_thread(queue_capacity_text)
    assert f"Ваших проектов в очереди сейчас: 1 (#{pid_a})." in text_a
    assert f"#{pid_b}" not in text_a and f"#{pid_b2}" not in text_a

    async def _runner(_action, _params, _app):
        return {}

    async def _done(_pid, _sid):
        return []

    qc = QuestionContext(runner=_runner, done_of=_done)
    # Вопрос идёт под арендатором сессии чата: внутреннее имя сессии несёт арендатора.
    with tenant_scope(None):
        facts = await qc.gather("сколько проектов идут одновременно?", None, None, scoped_session("q", b))
    fact = next(f for f in facts if f.action == "queue.capacity")
    assert f"Ваших проектов в очереди сейчас: 2 (#{pid_b}, #{pid_b2})." in fact.text
    assert f"#{pid_a}" not in fact.text
