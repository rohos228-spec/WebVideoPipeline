"""Ревью port/today, раунд 4, без живого Postgres.

* память «арендатору проход не нужен» сбрасывается на коммите правки его проекта
  (флаги в meta без смены статуса: ⏹, «ждём ▶», стоп семьи);
* сон после ошибок: когда будить и насколько можно помнить «не нужен»;
* полоса массовой фабрики без `auto_mode` прохода не требует;
* гейт очереди генерации — только по номерам того же арендатора;
* общая папка до разделения копируется арендатору один раз; чужой номер папки
  в правке или при создании проекта не затягивает чужую папку.

Межарендное поведение на живом Postgres — `tests/test_port_tenant_fixes_postgres.py`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.services import sidebar_layout as sl
from app.services.tenant import tenant_scope
from tests.test_port_tenant_fixes import A, B, _seed_tenant_projects

# ── память «проход не нужен» ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_serve_cache_drops_tenant_on_commit_of_its_project_and_keeps_on_rollback(monkeypatch) -> None:
    from sqlalchemy import select

    from app.db import session_scope
    from app.models import Project
    from app.services import work_routing as wr
    from app.settings import settings

    monkeypatch.setattr(settings, "allow_unisolated_tenants", True)
    await _seed_tenant_projects({931: A, 932: B})
    wr.reset_serve_cache()
    wr._negative_cache[A] = ((), float("inf"))
    wr._negative_cache[B] = ((), float("inf"))
    try:
        async with session_scope() as s:
            p = (await s.execute(select(Project).where(Project.id == 931))).scalar_one()
            p.meta = {"user_stop": True}
            await s.flush()
            await s.rollback()
        assert A in wr._negative_cache and B in wr._negative_cache
        async with session_scope() as s:
            p = (await s.execute(select(Project).where(Project.id == 931))).scalar_one()
            p.meta = {}
            await s.flush()
            # До коммита проверка воркера прочла бы старое — память ещё держится.
            assert A in wr._negative_cache
            await s.commit()
        assert A not in wr._negative_cache
        # Чужой арендатор не тронут.
        assert B in wr._negative_cache
    finally:
        wr.reset_serve_cache()


def test_reset_serve_cache_one_tenant_or_all() -> None:
    from app.services import work_routing as wr

    wr._negative_cache.update({None: ((), 1.0), A: ((), 1.0), B: ((), 1.0)})
    wr.reset_serve_cache(A)
    assert set(wr._negative_cache) == {None, B}
    wr.reset_serve_cache(None)
    assert set(wr._negative_cache) == {B}
    wr.reset_serve_cache()
    assert wr._negative_cache == {}


# ── сон после ошибок ─────────────────────────────────────────────────────────


def test_sleep_wake_in_reports_due_future_and_nothing() -> None:
    from app.models import Project, ProjectStatus
    from app.services.work_routing import _sleep_wake_in

    def _p(meta):
        return Project(status=ProjectStatus.paused, meta=meta)

    past = (datetime.now(UTC) - timedelta(seconds=5)).isoformat()
    future = (datetime.now(UTC) + timedelta(seconds=12)).isoformat()
    fs = {"last_running": ProjectStatus.scripting.value}
    assert _sleep_wake_in(_p({"step_failure": {**fs, "sleep_until": past}})) == 0
    left = _sleep_wake_in(_p({"step_failure": {**fs, "sleep_until": future}}))
    assert left is not None and 0 < left <= 12
    # Не спит, ⏹ или нечего запускать снова — будить нечего.
    assert _sleep_wake_in(_p({})) is None
    assert _sleep_wake_in(_p({"user_stop": True, "step_failure": {**fs, "sleep_until": past}})) is None
    assert _sleep_wake_in(_p({"step_failure": {"sleep_until": past}})) is None


# ── полоса массовой фабрики ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_new_mass_lane_needs_tick_only_with_auto_mode() -> None:
    from app.models import Project, ProjectStatus
    from app.services.work_routing import _new_needs_tick

    lane = Project(status=ProjectStatus.new, auto_mode=False, meta={"mass_parent_id": 1})

    class _S:
        async def get(self, model, pid):
            return Project(id=pid, meta={})

    assert await _new_needs_tick(_S(), lane) is False
    lane.auto_mode = True
    assert await _new_needs_tick(_S(), lane) is True
    lane.meta = {"mass_parent_id": 1, "project_child_manual": True}
    assert await _new_needs_tick(_S(), lane) is False


# ── гейт очереди генерации ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_gen_queue_gate_counts_only_same_tenant_queue(monkeypatch) -> None:
    from sqlalchemy import select

    from app.db import session_scope
    from app.models import Project
    from app.services import gen_queue as gq
    from app.settings import settings

    monkeypatch.setattr(settings, "allow_unisolated_tenants", True)
    await _seed_tenant_projects({941: A, 942: A, 943: B, 944: None, 945: None})
    queue = [941]
    monkeypatch.setattr(gq, "get_gen_queue", lambda: list(queue))
    async with session_scope() as s:
        rows = {
            p.id: p
            for p in (await s.execute(select(Project).where(Project.id.in_([942, 943, 944])))).scalars()
        }
        # Очередь А держит только проекты А; Б и владелец двигаются.
        assert await gq.project_gated_by_own_gen_queue(s, rows[942]) is True
        assert await gq.project_gated_by_own_gen_queue(s, rows[943]) is False
        assert await gq.project_gated_by_own_gen_queue(s, rows[944]) is False
        # Очередь владельца держит владельца, как прежде; пустая — никого.
        queue[:] = [945]
        assert await gq.project_gated_by_own_gen_queue(s, rows[944]) is True
        assert await gq.project_gated_by_own_gen_queue(s, rows[942]) is False
        queue[:] = []
        assert await gq.project_gated_by_own_gen_queue(s, rows[944]) is False


# ── папки ────────────────────────────────────────────────────────────────────


def test_legacy_folder_is_copied_once_even_for_late_placements() -> None:
    sl.save_layout(
        {
            "folders": [{"id": "old1", "name": "Общая до разделения", "order": 0}],
            "project_layout": {"2": {"folder_id": "old1", "order": 0}},
            "gen_queue": [],
        }
    )
    with tenant_scope(A):
        fid = sl.layout_for_api({2})["folders"][0]["id"]
        # Место, позже указавшее на прежний номер (старый файл), — в ту же копию.
        data = sl.load_layout()
        data["project_layout"]["5"] = {"folder_id": "old1", "order": 1}
        sl.save_layout(data)
        view = sl.layout_for_api({2, 5})
        assert [f["id"] for f in view["folders"]] == [fid]
        assert view["project_layout"]["5"]["folder_id"] == fid
        # Старая вкладка: PUT и создание проекта с прежним номером — тоже в копию.
        sl.update_layout(project_layout={"6": {"folder_id": "old1", "order": 2}}, own_ids={6})
        sl.ensure_project_layout(7, folder_id="old1")
        lay = sl.load_layout()["project_layout"]
        assert lay["6"]["folder_id"] == fid and lay["7"]["folder_id"] == fid
        assert len(sl.load_layout()["tenant_folders"][A]) == 1
    # У владельца общая папка на месте.
    assert [f["id"] for f in sl.load_layout()["folders"]] == ["old1"]


def test_tenant_cannot_pull_foreign_folder_by_id() -> None:
    sl.save_layout(
        {
            "folders": [{"id": "ownerf", "name": "Owner private", "order": 0}],
            "project_layout": {"1": {"folder_id": "ownerf", "order": 0}},
            "gen_queue": [],
        }
    )
    with tenant_scope(B):
        fb = sl.create_folder("Б")["id"]
    with tenant_scope(A):
        sl.update_layout(project_layout={"2": {"folder_id": "ownerf", "order": 0}}, own_ids={2})
        sl.update_layout(project_layout={"3": {"folder_id": fb, "order": 0}}, own_ids={3})
        sl.ensure_project_layout(4, folder_id="ownerf")
        view = sl.layout_for_api({2, 3, 4})
        # Имя папки владельца к арендатору не попадает ни сразу, ни при следующем чтении.
        assert view["folders"] == []
        assert sl.layout_for_api({2, 3, 4})["folders"] == []
        assert all(view["project_layout"][k]["folder_id"] is None for k in ("2", "3", "4"))
        # Своя папка — как прежде.
        fa = sl.create_folder("А")["id"]
        sl.ensure_project_layout(8, folder_id=fa)
        sl.update_layout(project_layout={"2": {"folder_id": fa, "order": 0}}, own_ids={2})
        lay = sl.load_layout()["project_layout"]
        assert lay["8"]["folder_id"] == fa and lay["2"]["folder_id"] == fa
    # Владелец — без проверки, его места на месте.
    sl.ensure_project_layout(9, folder_id="ownerf")
    lay = sl.load_layout()["project_layout"]
    assert lay["9"]["folder_id"] == "ownerf" and lay["1"]["folder_id"] == "ownerf"
    assert [f["name"] for f in sl.layout_for_api({1, 9})["folders"]] == ["Owner private"]


def test_duplicate_own_folder_id_in_put_keeps_placements_in_first_copy() -> None:
    sl.save_layout({"folders": [], "project_layout": {}, "gen_queue": []})
    with tenant_scope(B):
        out = sl.update_layout(
            folders=[{"id": "x1", "name": "Б1"}, {"id": "x1", "name": "Б2"}],
            project_layout={"3": {"folder_id": "x1", "order": 0}},
            own_ids={3},
        )
        ids = [f["id"] for f in out["tenant_folders"][B]]
        assert ids[0] == "x1" and len(set(ids)) == 2
        assert out["project_layout"]["3"]["folder_id"] == "x1"
