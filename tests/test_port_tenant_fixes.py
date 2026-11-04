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
    assert member == [
        "Одновременно идут не больше 2 проектов очереди; это общая настройка студии, её меняет админ."
    ]
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
        {
            (A, "", None, "plan", "default"): "ТЕКСТ КЛИЕНТА А",
            (None, "", None, "plan", "default"): "СИСТЕМНЫЙ",
        },
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
            return {
                "variant": "default",
                "text": "ТЕКСТ КЛИЕНТА",
                "text_level": "tenant",
                "text_level_ru": "мои",
            }

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


# ── вторая волна ревью: места очереди, папки, каталог, воркер ────────────────


def test_planner_select_catalog_hides_set_parallel_from_member(as_identity) -> None:
    from app.services.action_layer.planner import build_prompt
    from app.services.action_layer.planner_catalog import select_catalog
    from app.services.action_layer.spec import ensure_builtin_actions, registry

    ensure_builtin_actions()
    if registry.find("queue.set_parallel") is None:
        pytest.skip("queue.set_parallel не зарегистрирован")
    text = "поставь одновременно 3 проекта в очереди, параллель очереди"
    total = len(registry.list())
    as_identity(_admin())
    assert "queue.set_parallel" in select_catalog(text, None, registry, 12).ids
    # Каталог шире выборки — отдаётся всё; участнику и тогда без действия всей студии.
    assert "queue.set_parallel" in select_catalog(text, None, registry, total + 5).ids
    as_identity(_member())
    for max_catalog in (12, total + 5):
        assert "queue.set_parallel" not in select_catalog(text, None, registry, max_catalog).ids
    _messages, info = build_prompt(text, {"facts": {}}, registry, 4, max_catalog=total + 5)
    assert "queue.set_parallel" not in info["offered"]


def test_folders_are_per_tenant_and_owner_keeps_legacy_list() -> None:
    sl.save_layout(
        {
            "folders": [{"id": "own0", "name": "Старая папка владельца", "order": 0}],
            "project_layout": {
                "1": {"folder_id": "own0", "order": 0},
                "2": {"folder_id": None, "order": 0},
                "3": {"folder_id": None, "order": 1},
            },
            "gen_queue": [],
        }
    )
    with tenant_scope(A):
        fa = sl.create_folder("Папка А")
        # Чужие папки (владельца) клиенту не видны и не правятся.
        assert [f["name"] for f in sl.layout_for_api({2})["folders"]] == ["Папка А"]
        with pytest.raises(KeyError):
            sl.rename_folder("own0", "взлом")
        assert sl.delete_folder("own0") is False
    with tenant_scope(B):
        assert sl.layout_for_api({3})["folders"] == []
        with pytest.raises(KeyError):
            sl.rename_folder(fa["id"], "взлом")
        assert sl.delete_folder(fa["id"]) is False
        # Свой проект, положенный в номер чужой папки, — в корне у себя.
        sl.update_layout(project_layout={"3": {"folder_id": fa["id"], "order": 0}}, own_ids={3})
        assert sl.layout_for_api({3})["project_layout"]["3"]["folder_id"] is None
        # PUT папок от Б пишет только папки Б.
        sl.update_layout(folders=[{"id": "fb", "name": "Папка Б", "order": 0}], own_ids={3})
        assert [f["name"] for f in sl.layout_for_api({3})["folders"]] == ["Папка Б"]
    # Владелец: прежний общий список, без папок клиентов.
    owner = sl.layout_for_api({1, 2, 3})
    assert [f["name"] for f in owner["folders"]] == ["Старая папка владельца"]
    assert owner["project_layout"]["1"]["folder_id"] == "own0"
    with tenant_scope(A):
        sl.update_layout(project_layout={"2": {"folder_id": fa["id"], "order": 0}}, own_ids={2})
        assert sl.rename_folder(fa["id"], "А2")["name"] == "А2"
        assert sl.layout_for_api({2})["project_layout"]["2"]["folder_id"] == fa["id"]
        assert sl.delete_folder(fa["id"]) is True
        assert sl.layout_for_api({2})["project_layout"]["2"]["folder_id"] is None
    # Папки переживают запись другой части файла.
    sl.update_layout(gen_queue=[1])
    data = sl.load_layout()
    assert data["tenant_folders"] == {A: [], B: [{"id": "fb", "name": "Папка Б", "order": 0}]}
    assert [f["id"] for f in data["folders"]] == ["own0"]


def test_folder_delete_and_put_cannot_touch_foreign_placements() -> None:
    sl.save_layout(
        {
            "folders": [{"id": "own0", "name": "Папка владельца", "order": 0}],
            "project_layout": {
                "1": {"folder_id": "own0", "order": 0},
                "2": {"folder_id": None, "order": 0},
                "3": {"folder_id": None, "order": 0},
            },
            "gen_queue": [],
        }
    )
    with tenant_scope(A):
        # Номер чужой папки в PUT заменяется свежим, раскладка той же правки — за ним.
        out = sl.update_layout(
            folders=[{"id": "own0", "name": "А", "order": 0}],
            project_layout={"2": {"folder_id": "own0", "order": 0}},
            own_ids={2},
        )
        fid = out["tenant_folders"][A][0]["id"]
        assert fid != "own0"
        assert out["project_layout"]["2"]["folder_id"] == fid
        assert sl.delete_folder("own0", own_ids={2}) is False
        assert sl.delete_folder(fid, own_ids={2}) is True
        assert sl.load_layout()["project_layout"]["2"]["folder_id"] is None
    with tenant_scope(B):
        # Повтор своего номера в одном PUT тоже не делит папку надвое.
        sl.update_layout(folders=[{"id": "x1", "name": "Б1"}, {"id": "x1", "name": "Б2"}], own_ids={3})
        ids = [f["id"] for f in sl.load_layout()["tenant_folders"][B]]
        assert ids[0] == "x1" and len(set(ids)) == 2
    # Даже при совпавшем номере (старый файл) удаление арендатора чистит только его проекты.
    data = sl.load_layout()
    data["tenant_folders"][A] = [{"id": "own0", "name": "двойник", "order": 0}]
    data["project_layout"]["2"] = {"folder_id": "own0", "order": 0}
    sl.save_layout(data)
    with tenant_scope(A):
        assert sl.delete_folder("own0", own_ids={2}) is True
    lay = sl.load_layout()["project_layout"]
    assert lay["1"]["folder_id"] == "own0" and lay["2"]["folder_id"] is None
    # Новая папка не получает занятый номер.
    with tenant_scope(B):
        assert sl.create_folder("Б3")["id"] not in {"own0", "x1"}


def test_tenant_projects_in_legacy_shared_folder_get_a_copy() -> None:
    sl.save_layout(
        {
            "folders": [{"id": "old1", "name": "Общая до разделения", "order": 2}],
            "project_layout": {
                "1": {"folder_id": "old1", "order": 0},
                "2": {"folder_id": "old1", "order": 1},
                "3": {"folder_id": "old1", "order": 0},
            },
            "gen_queue": [],
        }
    )
    with tenant_scope(A):
        view = sl.layout_for_api({2})
        assert [f["name"] for f in view["folders"]] == ["Общая до разделения"]
        fid = view["folders"][0]["id"]
        assert fid != "old1" and view["project_layout"]["2"]["folder_id"] == fid
        # Повторный показ не плодит копий; удалить копию — только своё.
        assert [f["id"] for f in sl.layout_for_api({2})["folders"]] == [fid]
        assert sl.delete_folder(fid, own_ids={2}) is True
    # У владельца папка и его проекты на месте; проект другого арендатора — тоже.
    owner = sl.layout_for_api({1})
    assert [f["id"] for f in owner["folders"]] == ["old1"]
    assert owner["project_layout"]["1"]["folder_id"] == "old1"
    assert sl.load_layout()["project_layout"]["3"]["folder_id"] == "old1"


async def _seed_tenant_projects(ids_tenants: dict[int, str | None]) -> None:
    from app.db import session_scope
    from app.models import Project, ProjectStatus

    async with session_scope() as s:
        for pid, tenant in ids_tenants.items():
            s.add(
                Project(
                    id=pid,
                    slug=f"q-{pid}-{uuid.uuid4().hex[:6]}",
                    title=f"P{pid}",
                    topic="t",
                    status=ProjectStatus.new,
                    hero_mode="no_hero",
                    auto_mode=True,
                    tenant_id=tenant,
                )
            )
        await s.commit()


@pytest.mark.asyncio
async def test_queue_positions_and_size_count_only_own_projects(monkeypatch) -> None:
    from app.db import session_scope
    from app.services.action_layer import builtin_insight as bi
    from app.services.gen_queue import get_gen_queue_idle_info, own_gen_queue
    from app.settings import settings

    # SQLite без политик: арендатор — только сверкой tenant_id строки (как в _own_ids).
    monkeypatch.setattr(settings, "allow_unisolated_tenants", True)
    await _seed_tenant_projects({901: B, 902: A, 903: B, 904: A, 905: None})
    sl.set_gen_queue([901, 902, 903, 904, 905])
    async with session_scope() as s:
        assert await own_gen_queue(s) == [901, 902, 903, 904, 905]  # владелец — вся очередь
        with tenant_scope(A):
            assert await own_gen_queue(s) == [902, 904]
            idle = await get_gen_queue_idle_info(s)
    # Место простоя очереди — среди своих: #902 первый у А, хотя в общей очереди второй.
    assert idle is not None and idle["project_id"] == 902 and idle["position"] == 1

    with tenant_scope(A):
        out = await bi._projects_overview(None, {"status": "queue", "limit": 30})
    assert {p["id"]: p["queue_position"] for p in out["projects"]} == {902: 1, 904: 2}
    assert out["queue"]["size"] == 2
    assert "В очереди генерации 2," in out["text_ru"]
    out = await bi._projects_overview(None, {"status": "queue", "limit": 30})
    assert out["queue"]["size"] == 5


def test_worker_fallback_round_keeps_tenant_waits(monkeypatch) -> None:
    """Список «кому служим» не получен: запасной проход владельцу ожидания арендаторов не стирает."""
    import asyncio

    from app.main import _run_worker_loop
    from app.services import step_cancel as sc
    from app.services import work_routing
    from app.services.noop_bot import get_worker_bot
    from app.settings import Settings

    asked: list[int] = []

    async def _boom(*a, **kw):
        asked.append(1)
        raise RuntimeError("routes unavailable")

    import app.main as main_mod

    class _QuietLogger:
        # Трассировка исключения в лог форматируется долго — тик не успевал дойти до чистки.
        def exception(self, *a, **kw) -> None:
            return None

        def __getattr__(self, name):
            return getattr(real_logger, name)

    real_logger = main_mod.logger
    monkeypatch.setattr(main_mod, "logger", _QuietLogger())
    monkeypatch.setattr(Settings, "is_postgres", property(lambda self: True))
    monkeypatch.setattr(work_routing, "tenants_to_serve", _boom)
    sc._worker_waits.clear()

    async def _run() -> None:
        with tenant_scope(A):
            sc.note_worker_wait(77, "parallel_slots_full")
        task = asyncio.create_task(_run_worker_loop(get_worker_bot(None)))
        for _ in range(40):
            await asyncio.sleep(0.1)
            if asked:
                break
        # Проход владельцу после сбоя — в пределах того же тика; следующий тик через 5 с.
        await asyncio.sleep(1.0)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    try:
        asyncio.run(_run())
        assert asked, "петля не дошла до списка арендаторов"
        assert sc.worker_wait_reason(77) == "parallel_slots_full"
    finally:
        sc._worker_waits.clear()
