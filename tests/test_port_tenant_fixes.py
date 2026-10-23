"""Многоарендная линия: правки ревью port/today без живого Postgres.

* общая очередь генерации: правка арендатора меняет только его номера, ответ — только его;
* стоп очереди после ⏹ — свой у каждого арендатора;
* «ждёт места» воркера: записи арендаторов, которым тик не служит, чистятся;
* «N из M» в ожидании места — только среди планов своего арендатора;
* окно параллели очереди меняет только админ; участнику его не предлагают;
* текст промта ноды — тот, что пойдёт в шаг (база арендатора раньше диска);
* отчёт по проектам участнику — без сумм в $ и в kind="json";
* правка настроек без личности — отказ, а не исходные параметры.

Межарендное поведение на живом Postgres — `tests/test_port_tenant_fixes_postgres.py`.
"""

from __future__ import annotations

import uuid

import pytest

from app.services import sidebar_layout as sl
from app.services.tenant import tenant_scope

A = str(uuid.UUID(int=0xA))
B = str(uuid.UUID(int=0xB))


def _member(uid: str = A):
    from app.services.studio_auth import ROLE_MEMBER, StudioIdentity

    return StudioIdentity(user_id=uid, email="m@studio.local", role=ROLE_MEMBER)


def _admin(uid: str = A):
    from app.services.studio_auth import ROLE_ADMIN, StudioIdentity

    return StudioIdentity(user_id=uid, email="a@studio.local", role=ROLE_ADMIN)


@pytest.fixture
def as_identity(monkeypatch):
    """Режим учётных записей и личность текущего контекста; после теста — как было."""
    from app.services.studio_auth import current_identity, set_identity
    from app.settings import settings

    monkeypatch.setattr(settings, "studio_session_secret", "test-secret-" + "x" * 40)
    prev = current_identity()

    def _set(ident):
        set_identity(ident)
        return ident

    yield _set
    set_identity(prev)


# ── общая очередь генерации ──────────────────────────────────────────────────


def test_merge_own_queue_keeps_foreign_ids_and_order() -> None:
    current = [1, 10, 2, 11, 3]  # 10, 11 — чужие
    own = {1, 2, 3, 4}
    # Свой новый порядок занимает свои места, чужие стоят где стояли.
    assert sl.merge_own_queue(current, own, [3, 1, 2]) == [3, 10, 1, 11, 2]
    # Свой номер убран — его место уходит, лишний свой — в конец.
    assert sl.merge_own_queue(current, own, [2]) == [2, 10, 11]
    assert sl.merge_own_queue(current, own, [1, 2, 3, 4]) == [1, 10, 2, 11, 3, 4]
    # Чужой номер в своём списке в очередь не попадает и чужое не двигает.
    assert sl.merge_own_queue(current, own, [11, 10, 1]) == [1, 10, 11]
    # Режим владельца (видно всё): список и есть очередь — как раньше.
    assert sl.merge_own_queue(current, None, [11, 1]) == [11, 1]


def test_update_layout_under_tenant_touches_only_own_projects() -> None:
    sl.save_layout(
        {
            "folders": [],
            "project_layout": {"1": {"folder_id": None, "order": 0}, "10": {"folder_id": "f", "order": 3}},
            "gen_queue": [10, 1, 11],
            "gen_queue_halted": False,
        }
    )
    sl.update_layout(project_layout={"1": {"folder_id": None, "order": 5}}, gen_queue=[2, 1], own_ids={1, 2})
    data = sl.load_layout()
    assert data["gen_queue"] == [10, 2, 11, 1]
    assert data["project_layout"]["10"] == {"folder_id": "f", "order": 3}
    assert data["project_layout"]["1"] == {"folder_id": None, "order": 5}
    view = sl.own_queue_view(data["gen_queue"], {1, 2})
    assert view == {"gen_queue": [2, 1], "gen_queue_positions": {2: 1, 1: 2}}


def test_queue_halted_is_per_tenant() -> None:
    with tenant_scope(A):
        sl.set_gen_queue_halted(True, reason="STOP")
        assert sl.is_gen_queue_halted() is True
    with tenant_scope(B):
        assert sl.is_gen_queue_halted() is False
        sl.set_gen_queue_halted(True)
        assert sl.clear_gen_queue_halted(reason="enqueue") is True
        assert sl.is_gen_queue_halted() is False
    with tenant_scope(A):
        assert sl.is_gen_queue_halted() is True
    # Владелец (без арендатора) — прежний общий флаг, чужие стопы его не трогают.
    assert sl.is_gen_queue_halted() is False
    sl.set_gen_queue_halted(True)
    assert sl.load_layout()["gen_queue_halted"] is True
    with tenant_scope(B):
        assert sl.is_gen_queue_halted() is False
    # Флаги переживают запись другой части файла.
    sl.update_layout(gen_queue=[1])
    with tenant_scope(A):
        assert sl.is_gen_queue_halted() is True


# ── воркер: ожидания и место в очереди ───────────────────────────────────────


def test_stale_worker_waits_of_unserved_tenants_are_cleared() -> None:
    from app.services import step_cancel as sc

    sc._worker_waits.clear()
    try:
        with tenant_scope(A):
            sc.note_worker_wait(1, "parallel_slots_full")
        with tenant_scope(B):
            sc.note_worker_wait(2, "queue_predecessor")
        sc.note_worker_wait(3, "user_stop")
        sc.keep_worker_wait_tenants([None, B])
        assert sc.worker_wait_reason(1) is None
        assert sc.worker_wait_reason(2) == "queue_predecessor"
        assert sc.worker_wait_reason(3) == "user_stop"
        sc.keep_worker_wait_tenants([])
        assert sc._worker_waits == {}
    finally:
        sc._worker_waits.clear()


def test_slot_wait_rank_counts_only_own_tenant_plans() -> None:
    from app.services.action_layer import executor as ex

    ex._SLOT_WAITERS.clear()
    ex._SLOT_WAITER_TENANTS.clear()
    try:
        with tenant_scope(A):
            ex._note_slot_waiter("a1", 1.0)
        with tenant_scope(B):
            ex._note_slot_waiter("b1", 2.0)
            ex._note_slot_waiter("b2", 3.0)
        with tenant_scope(A):
            ex._note_slot_waiter("a2", 4.0)
        assert "(2 из 2)" in ex.slot_wait_text("a2", 3)
        assert "(1 из 2)" in ex.slot_wait_text("b1", 3)
        assert "не больше 3" in ex.slot_wait_text("b2", 3)
        ex._drop_slot_waiter("a1")
        assert "(1 из 1)" in ex.slot_wait_text("a2", 3)
        assert "a1" not in ex._SLOT_WAITER_TENANTS
    finally:
        ex._SLOT_WAITERS.clear()
        ex._SLOT_WAITER_TENANTS.clear()


# ── окно параллели очереди: только админ ─────────────────────────────────────


def test_set_parallel_is_admin_only(as_identity) -> None:
    from app.services.action_layer.plan import admin_only_message, admin_only_refused

    as_identity(_member())
    assert admin_only_refused("queue.set_parallel", {"workers": 3}) is True
    assert "меняет только админ" in admin_only_message("queue.set_parallel")
    as_identity(_admin())
    assert admin_only_refused("queue.set_parallel", {"workers": 3}) is False


def test_set_parallel_off_accounts_mode_stays_open() -> None:
    from app.services.action_layer.plan import admin_only_refused

    assert admin_only_refused("queue.set_parallel", {"workers": 3}) is False


@pytest.mark.asyncio
async def test_set_parallel_executor_refuses_member_and_reads_without_side_effects(
    as_identity, monkeypatch
) -> None:
    from app.services.action_layer import builtin_launch as bl
    from app.services.runtime_streams import save_runtime_streams

    calls: list[tuple[str, str]] = []

    class _Ctx:
        async def call(self, method, path, **kw):
            calls.append((method, path))
            if method == "PATCH":
                return {"worker_max_parallel": kw["json"]["worker_max_parallel"], "worker_busy": 0}
            raise AssertionError(f"лишний вызов {method} {path}")

    save_runtime_streams({"worker_max_parallel": 3})
    as_identity(_member())
    with pytest.raises(PermissionError, match="меняет только админ"):
        await bl._set_parallel(_Ctx(), {"workers": 2})
    assert calls == []

    as_identity(_admin())
    out = await bl._set_parallel(_Ctx(), {"workers": 2})
    # Прежнее число прочитано из настройки, без GET /runtime-streams (он сбрасывает слоты).
    assert calls == [("PATCH", "/runtime-streams")]
    assert out["before"] == 3 and out["worker_max_parallel"] == 2 and out["lowered"] is True


def test_catalog_hides_set_parallel_from_member(as_identity) -> None:
    from app.orchestrator.agents.action_catalog import _allowed_specs

    as_identity(_member())
    assert "queue.set_parallel" not in {s.id for s in _allowed_specs()}
    as_identity(_admin())
    ids = {s.id for s in _allowed_specs()}
    if "queue.set_parallel" not in ids:
        pytest.skip("queue.set_parallel нет в белом списке чата")


def test_queue_capacity_text_does_not_offer_change_to_member(as_identity, monkeypatch) -> None:
    from app.orchestrator.agents import question_context as qc

    monkeypatch.setattr("app.services.orch_tenancy.own_projects", lambda tenant, ids=None: [])
    as_identity(_member())
    with tenant_scope(A):
        text = qc.queue_capacity_text()
    assert "меняет только админ" in text and "просьбой в чате" not in text
    as_identity(_admin())
    with tenant_scope(A):
        assert "просьбой в чате" in qc.queue_capacity_text()


@pytest.mark.asyncio
async def test_queue_warnings_read_limit_without_runtime_streams_call(as_identity, monkeypatch) -> None:
    from app.services.action_layer import builtin_launch as bl
    from app.services.runtime_streams import save_runtime_streams

    async def _projects(ctx, ids):
        return []

    monkeypatch.setattr(bl, "_load_projects", _projects)

    class _Ctx:
        async def call(self, method, path, **kw):
            raise AssertionError(f"лишний вызов {method} {path}")

    save_runtime_streams({"worker_max_parallel": 2})
    as_identity(_member())
    member = await bl._queue_warnings(_Ctx(), {"project_ids": [1]})
    assert member == ["Одновременно идут не больше 2 проектов очереди; это общая настройка студии, её меняет админ."]
    as_identity(_admin())
    admin = await bl._queue_warnings(_Ctx(), {"project_ids": [1]})
    assert "просьбой в чате" in admin[0]


# ── текст промта ноды — тот, что работает ────────────────────────────────────


def _node_ctx(tmp_path, monkeypatch):
    from app.services import prompt_library as pl
    from app.services.change_control.targets import NodeCtx

    step_dir = tmp_path / "plan_prompts"
    step_dir.mkdir()
    (step_dir / "default.md").write_text("ТЕКСТ С ДИСКА", encoding="utf-8")
    monkeypatch.setattr(pl, "step_dir", lambda step: step_dir)
    state = {"meta": {}, "prompt_overrides": {}}
    return NodeCtx(1, "plan_1", "plan", "plan", state)


def test_prompt_base_shows_tenant_db_text_before_disk(tmp_path, monkeypatch) -> None:
    from app.services import prompt_store
    from app.services.change_control import targets

    nctx = _node_ctx(tmp_path, monkeypatch)
    monkeypatch.setattr(
        prompt_store,
        "_CACHE",
        {(A, "", None, "plan", "default"): "ТЕКСТ КЛИЕНТА А", (None, "", None, "plan", "default"): "СИСТЕМНЫЙ"},
    )
    with tenant_scope(A):
        got = targets.describe_prompt_base(nctx)
        assert got["text"] == "ТЕКСТ КЛИЕНТА А" and got["text_level"] == "tenant"
        assert targets._PROMPT.base_text(nctx) == "ТЕКСТ КЛИЕНТА А"
    with tenant_scope(B):
        got = targets.describe_prompt_base(nctx)
        assert got["text"] == "СИСТЕМНЫЙ" and got["text_level"] == "system"
    # Базы нет — файл на диске, как у read_prompt.
    monkeypatch.setattr(prompt_store, "_CACHE", {})
    with tenant_scope(A):
        got = targets.describe_prompt_base(nctx)
    assert got["text"] == "ТЕКСТ С ДИСКА" and got["text_level"] == "disk"


def test_prompt_base_matches_read_prompt(tmp_path, monkeypatch) -> None:
    from app.services import prompt_library as pl
    from app.services import prompt_store
    from app.services.change_control import targets

    nctx = _node_ctx(tmp_path, monkeypatch)
    monkeypatch.setattr(prompt_store, "_CACHE", {(B, "", None, "plan", "default"): "ТЕКСТ КЛИЕНТА Б"})
    for tenant in (A, B, None):
        with tenant_scope(tenant):
            assert targets.describe_prompt_base(nctx)["text"] == pl.read_prompt("plan", "default")


def test_prompt_missing_everywhere_stays_missing(tmp_path, monkeypatch) -> None:
    from app.services import prompt_store
    from app.services.change_control import targets

    nctx = _node_ctx(tmp_path, monkeypatch)
    (tmp_path / "plan_prompts" / "default.md").unlink()
    monkeypatch.setattr(prompt_store, "_CACHE", {})
    got = targets.describe_prompt_base(nctx)
    assert got["source"] == "missing" and "не найден" in got["missing_reason"]


@pytest.mark.asyncio
async def test_node_describe_names_tenant_prompt_level() -> None:
    from app.services.action_layer import builtin_node_edit as ne

    class _Ctx:
        async def call(self, method, path, **kw):
            if path.endswith("/describe"):
                return {"text_ru": "Нода «План»."}
            return {"variant": "default", "text": "ТЕКСТ КЛИЕНТА", "text_level": "tenant", "text_level_ru": "мои"}

    out = await ne._describe(_Ctx(), {"project_id": 1, "node_key": "plan_1"})
    assert "Текст промта «default» (мои):\nТЕКСТ КЛИЕНТА" in out["text_ru"]


# ── отчёт по проектам участнику ──────────────────────────────────────────────


def test_member_project_row_has_no_money() -> None:
    from app.services.action_layer.report_gpt import _member_row

    row = {
        "project_id": 1,
        "found": True,
        "spent_usd": 1.5,
        "spent_by_source": {"kie": 1.5},
        "attributed_usd": 1.0,
        "gpt_nodes": [{"node_key": "g", "cost_usd": 0.4, "cost_how": "окно", "label": "GPT"}],
    }
    got = _member_row(row)
    assert not {"spent_usd", "spent_by_source", "attributed_usd"} & set(got)
    assert got["gpt_nodes"] == [{"node_key": "g", "label": "GPT"}]


@pytest.mark.asyncio
async def test_member_report_projects_json_has_no_money(as_identity, monkeypatch) -> None:
    from app.services.action_layer import report_gpt as rg

    async def _ids(params):
        return [1], 1

    async def _grouped(pids):
        return {"frames": {}, "images": {}, "videos": {}, "spent": {}}

    async def _row(ctx, pid, row, counts):
        return {
            "project_id": pid,
            "found": True,
            "title": "Лисы",
            "frames": 0,
            "images": 0,
            "videos": 0,
            "spent_usd": None,
            "spent_by_source": {},
            "attributed_usd": 0.0,
            "gpt_nodes": [{"node_key": "g", "cost_usd": None}],
        }

    monkeypatch.setattr(rg, "_project_ids", _ids)
    monkeypatch.setattr(rg, "_grouped", _grouped)
    monkeypatch.setattr(rg, "_project_row_report", _row)
    as_identity(_member())
    out = await rg.report_projects(object(), {"kind": "json"})
    assert "spent_usd_estimate" not in out["summary"]
    assert all("spent_usd" not in r and "attributed_usd" not in r for r in out["rows"])
    assert all("cost_usd" not in n for r in out["rows"] for n in r["gpt_nodes"])
    assert "траты" not in out["text_ru"]


# ── правка настроек без личности ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_changed_settings_only_refuses_without_identity(as_identity, monkeypatch) -> None:
    from app.orchestrator.contracts import PlanResolveRefused
    from app.services.action_layer import builtin_wave5 as w5

    async def _never(ctx, pid):
        raise AssertionError("без личности проект не читается")

    monkeypatch.setattr(w5, "_get_project", _never)
    as_identity(None)
    with pytest.raises(PlanResolveRefused, match="без входа"):
        await w5.changed_settings_only({"project_id": 5, "title": "x", "image_generator": "g"})


@pytest.mark.asyncio
async def test_changed_settings_only_refuses_on_unauthorized_read(as_identity, monkeypatch) -> None:
    from app.orchestrator.contracts import PlanResolveRefused
    from app.services.action_layer import builtin_wave5 as w5
    from app.services.action_layer.context import ActionCallError

    async def _401(ctx, pid):
        raise ActionCallError(401, "внутренний вызов без личности")

    monkeypatch.setattr(w5, "_get_project", _401)
    monkeypatch.setattr("app.orchestrator.app_ref.current", lambda: object())
    as_identity(_member())
    with pytest.raises(PlanResolveRefused):
        await w5.changed_settings_only({"project_id": 5, "title": "x"})
