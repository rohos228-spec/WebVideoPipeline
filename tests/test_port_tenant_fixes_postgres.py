"""Правки ревью port/today на живом Postgres с двумя арендаторами.

* воркер служит арендатору, у которого в очереди генерации только ждущие проекты
  (раньше — только тем, у кого шаг уже идёт: очередь отвечала «поставлено» и стояла);
  `gen_queue_tick` под арендатором стартует только его проекты;
* общий файл очереди: массовая постановка и порядок одного клиента не выбрасывают
  номера другого, ответы ручек — только свои номера; стоп очереди — у каждого свой;
* текст промта ноды (`/change-control/prompt-base`, `node.describe`) — из базы своего
  арендатора, как его прочтёт шаг;
* окно параллели очереди меняет только админ.

Postgres подаётся как для `tests/test_orch_tenant_postgres.py` (``TEST_DATABASE_URL``,
обычная роль). Пропускается без ``TEST_DATABASE_URL``.
"""

from __future__ import annotations

import uuid

import pytest

from tests.orch_pg_support import TEST_DB_URL
from tests.test_orch_tenant_postgres import pg  # noqa: F401  фикстура

pytestmark = pytest.mark.skipif(not TEST_DB_URL, reason="TEST_DATABASE_URL не задан — живого Postgres нет")


async def _project_of(pg, tenant: str, *, title: str, status=None, meta: dict | None = None) -> int:  # noqa: F811
    from app.models import Project, ProjectStatus
    from app.services.tenant import tenant_scope

    with tenant_scope(tenant):
        async with pg["factory"]() as s:
            p = Project(
                slug=f"pf-{uuid.uuid4().hex[:8]}",
                title=title,
                topic="t",
                status=status or ProjectStatus.new,
                hero_mode="no_hero",
                auto_mode=True,
                meta=dict(meta or {}),
            )
            s.add(p)
            await s.commit()
            return p.id


# ── воркер: кому служить ─────────────────────────────────────────────────────


async def test_worker_serves_tenant_with_only_queued_idle_projects(pg, monkeypatch) -> None:  # noqa: F811
    from app.models import ProjectStatus
    from app.orchestrator.auto_advance import TRANSITIONS
    from app.orchestrator.node_registry import WORKER_ACTIVE_STATUSES
    from app.services import gen_queue as gq
    from app.services.tenant import tenant_scope
    from app.services.work_routing import tenants_to_serve, tenants_with_active_work

    a, b = pg["a"].user_id, pg["b"].user_id
    pid_a = await _project_of(pg, a, title="A в очереди")  # new: в работе ничего нет
    pid_b = await _project_of(pg, b, title="B ждёт автопродвижения", status=ProjectStatus.plan_ready)
    running = [s.value for s in WORKER_ACTIVE_STATUSES]

    with tenant_scope(None):
        async with pg["factory"]() as s:
            before = await tenants_with_active_work(s, running)
            served = await tenants_to_serve(s, running, [pid_a])
            with_ready = await tenants_to_serve(s, running + [st.value for st in TRANSITIONS], [pid_a])
    # Прежний список работы клиента А не видел вовсе — его очередь не стартовала никогда.
    assert a not in before and b not in before
    # База между тестами не чистится: арендаторы прошлых тестов тоже могут быть в списке.
    assert a in served and b not in served
    assert {a, b} <= set(with_ready)

    # Проход под арендатором стартует только его номера очереди.
    queue = [pid_b, pid_a]
    monkeypatch.setattr(gq, "get_gen_queue", lambda: list(queue))
    monkeypatch.setattr(gq, "is_gen_queue_halted", lambda: False)
    monkeypatch.setattr(gq, "worker_max_parallel", lambda: 2)
    started: list[tuple[str | None, int]] = []

    async def _start(session, project, *, queue_pos):
        from app.services.tenant import current_tenant

        started.append((current_tenant(), project.id))
        return 1

    async def _not_done(session, project, *, queue_pos):
        return False

    monkeypatch.setattr(gq, "_start_or_advance_project", _start)
    monkeypatch.setattr(gq, "_close_slot_if_already_at_target", _not_done)
    from app.db import session_scope

    for tenant in (a, b):
        with tenant_scope(tenant):
            async with session_scope() as s:
                await gq.gen_queue_tick(s)
    assert (a, pid_a) in started
    assert all(owner == (a if pid == pid_a else b) for owner, pid in started)


# ── общий файл очереди ───────────────────────────────────────────────────────


async def test_queue_writes_keep_other_tenant_ids_and_responses_show_only_own(pg) -> None:  # noqa: F811
    from app.services import sidebar_layout as sl

    a, b = pg["a"].user_id, pg["b"].user_id
    a1 = await _project_of(pg, a, title="A1")
    a2 = await _project_of(pg, a, title="A2")
    b1 = await _project_of(pg, b, title="B1")
    b2 = await _project_of(pg, b, title="B2")
    client = pg["client"]
    url = "/api/sidebar-layout/gen-queue/bulk-enqueue"

    res = await client.post(url, json={"project_ids": [b1, b2]}, headers=pg["b"].auth)
    assert res.status_code == 200, res.text
    res = await client.post(url, json={"project_ids": [a1, a2]}, headers=pg["a"].auth)
    assert res.status_code == 200, res.text
    body = res.json()
    # Массовая постановка А не выбросила номера Б; ответ — только свои номера и места.
    assert sl.get_gen_queue() == [b1, b2, a1, a2]
    assert body["gen_queue"] == [a1, a2]
    assert {int(k): v for k, v in body["gen_queue_positions"].items()} == {a1: 1, a2: 2}

    # Порядок от А: свои места переставлены, чужие на месте. Чужой номер в списке игнорируется.
    res = await client.put("/api/sidebar-layout", json={"gen_queue": [a2, b2, a1]}, headers=pg["a"].auth)
    assert res.status_code == 200, res.text
    assert sl.get_gen_queue() == [b1, b2, a2, a1]
    assert res.json()["gen_queue"] == [a2, a1]
    # Пустой список от Б снимает только его номера.
    res = await client.put("/api/sidebar-layout", json={"gen_queue": []}, headers=pg["b"].auth)
    assert res.status_code == 200, res.text
    assert sl.get_gen_queue() == [a2, a1]

    # Постановка одного проекта и снятие: ответ без чужих номеров.
    res = await client.post("/api/sidebar-layout/gen-queue/enqueue", json={"project_id": b1}, headers=pg["b"].auth)
    assert res.status_code == 200, res.text
    assert res.json()["gen_queue"] == [b1] and res.json()["position"] == 1
    res = await client.post("/api/sidebar-layout/gen-queue/toggle", json={"project_id": b1}, headers=pg["b"].auth)
    assert res.status_code == 200, res.text
    assert res.json()["gen_queue"] == [] and res.json()["position"] is None
    res = await client.get("/api/sidebar-layout", headers=pg["b"].auth)
    assert res.json()["gen_queue"] == []
    assert sl.get_gen_queue() == [a2, a1]


async def test_queue_halt_is_per_tenant(pg) -> None:  # noqa: F811
    from app.services import sidebar_layout as sl
    from app.services.tenant import tenant_scope

    a, b = pg["a"].user_id, pg["b"].user_id
    b1 = await _project_of(pg, b, title="B1")
    with tenant_scope(a):
        sl.set_gen_queue_halted(True, reason="STOP A")
    # Постановка Б снимает только свой стоп; стоп А остаётся.
    res = await pg["client"].post(
        "/api/sidebar-layout/gen-queue/enqueue", json={"project_id": b1}, headers=pg["b"].auth
    )
    assert res.status_code == 200, res.text
    with tenant_scope(a):
        assert sl.is_gen_queue_halted() is True
    with tenant_scope(b):
        assert sl.is_gen_queue_halted() is False


# ── текст промта ноды ────────────────────────────────────────────────────────


async def test_prompt_base_shows_own_tenant_db_prompt(pg, monkeypatch) -> None:  # noqa: F811
    from app.services import prompt_store

    a, b = pg["a"].user_id, pg["b"].user_id
    canvas = {"canvas_graph": {"nodes": [{"id": "plan_1", "type": "plan"}], "edges": []}}
    pid_a = await _project_of(pg, a, title="A", meta=canvas)
    pid_b = await _project_of(pg, b, title="B", meta=canvas)
    client = pg["client"]
    # Какой вариант выбран у ноды, решает студия (активный вариант шага); текст варианта —
    # уже цепочка базы: проект → арендатор → системный → диск.
    monkeypatch.setattr(prompt_store, "_CACHE", {})
    res = await client.get(
        "/api/change-control/prompt-base", params={"project_id": pid_a, "node_key": "plan_1"}, headers=pg["a"].auth
    )
    assert res.status_code == 200, res.text
    name = res.json()["variant"]
    monkeypatch.setattr(
        prompt_store,
        "_CACHE",
        {
            (a, "", None, "plan", name): "ПЛАН КЛИЕНТА А",
            (None, "", None, "plan", name): "СИСТЕМНЫЙ ПЛАН",
        },
    )
    res = await client.get(
        "/api/change-control/prompt-base", params={"project_id": pid_a, "node_key": "plan_1"}, headers=pg["a"].auth
    )
    assert res.status_code == 200, res.text
    assert res.json()["text"] == "ПЛАН КЛИЕНТА А" and res.json()["text_level"] == "tenant"
    res = await client.get(
        "/api/change-control/prompt-base", params={"project_id": pid_b, "node_key": "plan_1"}, headers=pg["b"].auth
    )
    assert res.status_code == 200, res.text
    assert res.json()["text"] == "СИСТЕМНЫЙ ПЛАН" and "КЛИЕНТА А" not in res.text
    # Чужой проект — не найден, текста нет.
    res = await client.get(
        "/api/change-control/prompt-base", params={"project_id": pid_a, "node_key": "plan_1"}, headers=pg["b"].auth
    )
    assert res.status_code == 404, res.text
    assert "КЛИЕНТА А" not in res.text


# ── окно параллели ───────────────────────────────────────────────────────────


async def test_only_admin_changes_studio_parallel(pg) -> None:  # noqa: F811
    from app.services.runtime_streams import get_worker_max_parallel, save_runtime_streams

    save_runtime_streams({"worker_max_parallel": 2})
    client = pg["client"]
    res = await client.patch("/api/runtime-streams", json={"worker_max_parallel": 4}, headers=pg["a"].auth)
    assert res.status_code == 403, res.text
    assert get_worker_max_parallel() == 2
    res = await client.patch("/api/runtime-streams", json={"worker_max_parallel": 3}, headers=pg["admin"].auth)
    assert res.status_code == 200, res.text
    assert get_worker_max_parallel() == 3
