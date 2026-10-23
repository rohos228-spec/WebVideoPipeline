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
    res = await client.post(
        "/api/sidebar-layout/gen-queue/enqueue", json={"project_id": b1}, headers=pg["b"].auth
    )
    assert res.status_code == 200, res.text
    assert res.json()["gen_queue"] == [b1] and res.json()["position"] == 1
    res = await client.post(
        "/api/sidebar-layout/gen-queue/toggle", json={"project_id": b1}, headers=pg["b"].auth
    )
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
        "/api/change-control/prompt-base",
        params={"project_id": pid_a, "node_key": "plan_1"},
        headers=pg["a"].auth,
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
        "/api/change-control/prompt-base",
        params={"project_id": pid_a, "node_key": "plan_1"},
        headers=pg["a"].auth,
    )
    assert res.status_code == 200, res.text
    assert res.json()["text"] == "ПЛАН КЛИЕНТА А" and res.json()["text_level"] == "tenant"
    res = await client.get(
        "/api/change-control/prompt-base",
        params={"project_id": pid_b, "node_key": "plan_1"},
        headers=pg["b"].auth,
    )
    assert res.status_code == 200, res.text
    assert res.json()["text"] == "СИСТЕМНЫЙ ПЛАН" and "КЛИЕНТА А" not in res.text
    # Чужой проект — не найден, текста нет.
    res = await client.get(
        "/api/change-control/prompt-base",
        params={"project_id": pid_a, "node_key": "plan_1"},
        headers=pg["b"].auth,
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
    res = await client.patch(
        "/api/runtime-streams", json={"worker_max_parallel": 3}, headers=pg["admin"].auth
    )
    assert res.status_code == 200, res.text
    assert get_worker_max_parallel() == 3


# ── вторая волна ревью ───────────────────────────────────────────────────────


async def test_queue_positions_are_counted_among_own_projects(pg) -> None:  # noqa: F811
    from app.services import sidebar_layout as sl
    from app.services.action_layer import builtin_insight as bi
    from app.services.tenant import tenant_scope

    a, b = pg["a"].user_id, pg["b"].user_id
    b1 = await _project_of(pg, b, title="B1")
    a1 = await _project_of(pg, a, title="A1")
    b2 = await _project_of(pg, b, title="B2")
    a2 = await _project_of(pg, a, title="A2")
    sl.set_gen_queue([b1, a1, b2, a2])
    client = pg["client"]

    # GET /projects: место среди своих — #a1 первый, #a2 второй (в общей очереди 2 и 4).
    res = await client.get("/api/projects", headers=pg["a"].auth)
    assert res.status_code == 200, res.text
    pos = {row["id"]: row["gen_queue_position"] for row in res.json()}
    assert pos[a1] == 1 and pos[a2] == 2 and b1 not in pos

    # GET /sidebar-layout: простой очереди — тоже место среди своих.
    res = await client.get("/api/sidebar-layout", headers=pg["a"].auth)
    assert res.status_code == 200, res.text
    idle = res.json()["gen_queue_idle"]
    assert idle is not None and idle["project_id"] == a1 and idle["position"] == 1
    res = await client.get("/api/sidebar-layout", headers=pg["b"].auth)
    idle = res.json()["gen_queue_idle"]
    assert idle["project_id"] == b1 and idle["position"] == 1

    # Чат «что в очереди»: места и размер очереди — только свои.
    with tenant_scope(a):
        out = await bi._projects_overview(None, {"status": "queue", "limit": 30})
    assert {p["id"]: p["queue_position"] for p in out["projects"]} == {a1: 1, a2: 2}
    assert out["queue"]["size"] == 2 and "В очереди генерации 2," in out["text_ru"]


async def test_sidebar_folders_are_per_tenant(pg) -> None:  # noqa: F811
    from app.services import sidebar_layout as sl

    a, b = pg["a"].user_id, pg["b"].user_id
    a1 = await _project_of(pg, a, title="A1")
    b1 = await _project_of(pg, b, title="B1")
    # Старые общие папки остаются у владельца.
    data = sl.load_layout()
    data["folders"] = [{"id": "legacy1", "name": "Папка владельца", "order": 0}]
    sl.save_layout(data)
    client = pg["client"]

    res = await client.post("/api/sidebar-layout/folders", json={"name": "Клиент А"}, headers=pg["a"].auth)
    assert res.status_code == 200, res.text
    fa = res.json()["id"]
    res = await client.get("/api/sidebar-layout", headers=pg["a"].auth)
    assert [f["name"] for f in res.json()["folders"]] == ["Клиент А"]
    res = await client.get("/api/sidebar-layout", headers=pg["b"].auth)
    assert res.json()["folders"] == [] and "Клиент А" not in res.text and "Папка владельца" not in res.text

    # Чужую папку не переименовать и не удалить.
    res = await client.patch(
        f"/api/sidebar-layout/folders/{fa}", json={"name": "взлом"}, headers=pg["b"].auth
    )
    assert res.status_code == 404, res.text
    res = await client.delete(f"/api/sidebar-layout/folders/{fa}", headers=pg["b"].auth)
    assert res.status_code == 404, res.text
    res = await client.delete("/api/sidebar-layout/folders/legacy1", headers=pg["a"].auth)
    assert res.status_code == 404, res.text

    # PUT папок от Б пишет только папки Б: папки А и владельца на месте.
    res = await client.put(
        "/api/sidebar-layout",
        json={
            "folders": [{"id": "fb1", "name": "Клиент Б", "order": 0}],
            "project_layout": {str(b1): {"folder_id": fa, "order": 0}},
        },
        headers=pg["b"].auth,
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert [f["name"] for f in body["folders"]] == ["Клиент Б"]
    # Номер чужой папки — проект Б в корне у себя.
    assert body["project_layout"][str(b1)]["folder_id"] is None
    res = await client.get("/api/projects", headers=pg["b"].auth)
    row = next(r for r in res.json() if r["id"] == b1)
    assert row["sidebar_folder_id"] is None

    res = await client.put(
        "/api/sidebar-layout",
        json={"project_layout": {str(a1): {"folder_id": fa, "order": 0}}},
        headers=pg["a"].auth,
    )
    assert res.status_code == 200, res.text
    res = await client.get("/api/sidebar-layout", headers=pg["a"].auth)
    assert [f["name"] for f in res.json()["folders"]] == ["Клиент А"]
    assert res.json()["project_layout"][str(a1)]["folder_id"] == fa
    data = sl.load_layout()
    assert [f["id"] for f in data["folders"]] == ["legacy1"]
    assert [f["name"] for f in data["tenant_folders"][str(b)]] == ["Клиент Б"]
    assert [f["id"] for f in data["tenant_folders"][str(a)]] == [fa]


async def _serve(pg, ids=()):  # noqa: F811
    from app.orchestrator.node_registry import WORKER_ACTIVE_STATUSES
    from app.services.tenant import tenant_scope
    from app.services.work_routing import reset_serve_cache, tenants_to_serve, waiting_statuses

    reset_serve_cache()
    with tenant_scope(None):
        async with pg["factory"]() as s:
            return await tenants_to_serve(
                s,
                [st.value for st in WORKER_ACTIVE_STATUSES],
                list(ids),
                waiting=waiting_statuses(),
                session_factory=pg["factory"],
            )


async def _set(pg, tenant, pid, **values) -> None:  # noqa: F811
    from sqlalchemy import update

    from app.models import Project
    from app.services.tenant import tenant_scope

    with tenant_scope(tenant):
        async with pg["factory"]() as s:
            await s.execute(update(Project).where(Project.id == pid).values(**values))
            await s.commit()


async def test_worker_serves_waiting_only_with_auto_mode_and_not_stopped(pg) -> None:  # noqa: F811
    from app.models import ProjectStatus
    from app.services.work_routing import waiting_statuses

    a, b = pg["a"].user_id, pg["b"].user_id
    pid_a = await _project_of(pg, a, title="A ждёт, auto выключен", status=ProjectStatus.plan_ready)
    await _set(pg, a, pid_a, auto_mode=False)
    await _project_of(pg, a, title="A ждёт, ⏹", status=ProjectStatus.script_ready, meta={"user_stop": True})
    await _project_of(
        pg,
        a,
        title="A ждёт ручной ▶",
        status=ProjectStatus.images_ready,
        meta={"auto_await_manual_start": True},
    )
    await _project_of(pg, a, title="A опубликован", status=ProjectStatus.published)
    pid_b = await _project_of(pg, b, title="B ждёт с auto", status=ProjectStatus.images_ready)
    waiting = waiting_statuses()
    assert "assembled" in waiting and "plan_ready" in waiting and "published" not in waiting

    served = await _serve(pg)
    # А: ждущий без auto_mode, с ⏹, ждущий ▶ и опубликованный — прохода нет; Б — есть.
    assert b in served and a not in served
    # Номер в очереди — проход есть всегда (очередь стартует его сама).
    queued = await _serve(pg, [pid_a])
    assert a in queued and b in queued
    assert pid_b  # noqa: S101


async def test_worker_serves_assembled_auto_project_with_publish_on_canvas(pg, monkeypatch) -> None:  # noqa: F811
    from app.models import Project, ProjectStatus
    from app.orchestrator import auto_advance
    from app.services.tenant import tenant_scope

    a, b = pg["a"].user_id, pg["b"].user_id
    pid_a = await _project_of(pg, a, title="A собран, дальше публикация", status=ProjectStatus.assembled)
    pid_b = await _project_of(pg, b, title="B собран, auto выключен", status=ProjectStatus.assembled)
    await _set(pg, b, pid_b, auto_mode=False)
    served = await _serve(pg)
    # Канвас по умолчанию кончается публикацией: собранный с auto_mode двигать есть куда.
    assert a in served and b not in served
    with tenant_scope(a):
        async with pg["factory"]() as s:
            p = await s.get(Project, pid_a)
            assert (
                await auto_advance._graph_next_running(s, p, ProjectStatus.assembled)
                is ProjectStatus.publishing
            )

    # Канвас без шага после сборки — покой, прохода нет.
    async def _no_next(session, project, ready_status):
        return None

    monkeypatch.setattr(auto_advance, "_graph_next_running", _no_next)
    assert a not in await _serve(pg)


async def test_worker_serves_next_mass_lane_and_running_batch(pg) -> None:  # noqa: F811
    from sqlalchemy import update

    from app.models import BatchProject, BatchStatus, ProjectStatus
    from app.services.project_control import MASS_FAMILY_HALTED_KEY
    from app.services.tenant import tenant_scope

    a, b = pg["a"].user_id, pg["b"].user_id
    # А: первая полоса фабрики собрана (auto выключен у родителя), следующая — в `new`.
    parent = await _project_of(pg, a, title="A родитель", status=ProjectStatus.assembled)
    await _set(pg, a, parent, auto_mode=False)
    lane1 = await _project_of(
        pg, a, title="A полоса 1", status=ProjectStatus.assembled, meta={"mass_parent_id": parent}
    )
    await _set(pg, a, lane1, auto_mode=False)
    await _project_of(pg, a, title="A полоса 2", meta={"mass_parent_id": parent, "mass_lane_position": 2})
    # Б: просто новый проект — прохода нет.
    await _project_of(pg, b, title="B новый")
    served = await _serve(pg)
    assert a in served and b not in served

    # Семья остановлена ⏹ — полосы не стартуют, проход не нужен.
    await _set(pg, a, parent, meta={MASS_FAMILY_HALTED_KEY: True})
    assert a not in await _serve(pg)

    # Б: массовый запущен, подпроект в `new` — проход есть; на паузе — нет.
    with tenant_scope(b):
        async with pg["factory"]() as s:
            batch = BatchProject(name="Б масс", slug=f"bm-{uuid.uuid4().hex[:8]}", status=BatchStatus.running)
            s.add(batch)
            await s.commit()
            batch_id = batch.id
    sub = await _project_of(pg, b, title="B подпроект")
    await _set(pg, b, sub, batch_id=batch_id)
    assert b in await _serve(pg)
    with tenant_scope(b):
        async with pg["factory"]() as s:
            await s.execute(
                update(BatchProject).where(BatchProject.id == batch_id).values(status=BatchStatus.paused)
            )
            await s.commit()
    assert b not in await _serve(pg)


async def test_worker_serve_check_remembers_no_for_same_candidates(pg, monkeypatch) -> None:  # noqa: F811
    from app.models import ProjectStatus
    from app.orchestrator.node_registry import WORKER_ACTIVE_STATUSES
    from app.services import work_routing as wr
    from app.services.tenant import tenant_scope

    a = pg["a"].user_id
    pid = await _project_of(pg, a, title="A ждёт, auto выключен", status=ProjectStatus.plan_ready)
    await _set(pg, a, pid, auto_mode=False)
    calls: list = []
    real = wr._tenant_needs_tick

    async def _counted(factory, tenant, waiting_ids, new_ids):
        calls.append(tenant)
        return await real(factory, tenant, waiting_ids, new_ids)

    monkeypatch.setattr(wr, "_tenant_needs_tick", _counted)
    running = [st.value for st in WORKER_ACTIVE_STATUSES]

    async def _tick():
        with tenant_scope(None):
            async with pg["factory"]() as s:
                return await wr.tenants_to_serve(
                    s, running, [], waiting=wr.waiting_statuses(), session_factory=pg["factory"]
                )

    wr.reset_serve_cache()
    assert a not in await _tick()
    assert a not in await _tick()
    # Второй тик с теми же кандидатами — без запроса от имени А.
    assert calls.count(a) == 1
    # Статус кандидата сменился — проверка повторяется сразу.
    await _set(pg, a, pid, status=ProjectStatus.script_ready, auto_mode=True)
    assert a in await _tick()
    assert calls.count(a) == 2
