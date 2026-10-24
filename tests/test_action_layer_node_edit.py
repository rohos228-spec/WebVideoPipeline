from __future__ import annotations

import copy
import json

import pytest

from app.models import Project, ProjectStatus
from app.services import node_edit as ne
from app.services.action_layer import executor
from app.services.action_layer.spec import ensure_builtin_actions, registry
from app.services.change_control import service as cc_service
from app.services.change_control import store as cs
from app.services.change_control.errors import ChangeRefused
from app.services.project_meta import merge_project_meta
from tests.action_layer_support import env
from tests.change_control_support import HUMAN, cc, prompts_root

AL = "/api/action-layer"
FORCED = "n_excel_gpt_fw_shots"

NODES = [
    {"id": "n_plan", "type": "plan", "data": {"label": "Сценарий"}},
    {"id": "n_script", "type": "script", "data": {"label": "Закадровый текст",
                                                  "modelId": "gpt-5.6-sol"}},
    {"id": "n_script_2", "type": "script", "data": {"label": "Второй закадр"}},
    {"id": "n_review", "type": "excel_gpt", "data": {"label": "Проверка", "slotIndex": 1}},
    {"id": "n_fix", "type": "excel_gpt", "data": {"label": "Правка", "slotIndex": 2}},
    {"id": "n_images", "type": "images", "data": {"label": "Картинки"}},
    {"id": "n_audio", "type": "audio", "data": {"label": "Озвучка"}},
    {"id": FORCED, "type": "excel_gpt", "data": {"label": "Кадры QC"}},
]
EDGES = [
    {"id": "e1", "source": "n_plan", "target": "n_script"},
    {"id": "e2", "source": "n_script", "target": "n_review"},
    {"id": "e3", "source": "n_review", "target": "n_fix", "data": {"kind": "after"}},
    {"id": "e4", "source": "n_fix", "target": "n_images"},
]


def _meta(**extra) -> dict:
    meta = {"canvas_graph": {"workflow_id": 1, "nodes": copy.deepcopy(NODES),
                             "edges": copy.deepcopy(EDGES)},
            "excel_gpt_nodes": {"n_review": {"role": "review"}}}
    meta.update(extra)
    return meta


def _state(**extra) -> dict:
    return {"id": 1, "meta": _meta(**extra), "prompt_overrides": {}}


def _node(meta: dict, key: str) -> dict:
    return ne.find_node(meta, key)


@pytest.fixture(autouse=True)
def _builtin():
    ensure_builtin_actions()


@pytest.fixture(autouse=True)
def _no_env_profile(monkeypatch):
    monkeypatch.delenv("STEP_FAILURE_WAIT_PROFILE", raising=False)



@pytest.mark.xfail(strict=True, reason='merge line: int/chat-v1 baseline test, model catalog / prompt library / meta agent of main differ')
def test_model_change_cost_classes() -> None:
    meta = _meta()
    cheap = ne.plan_model_change(meta, "n_script", "gpt-5.6-luna")
    assert cheap["cost"] == "cheaper" and cheap["needs_confirmation"] is False
    same = ne.plan_model_change(meta, "n_script", "gpt-5.5")
    assert same["cost"] == "same" and same["needs_confirmation"] is False
    dear = ne.plan_model_change(meta, "n_script", "claude-fable-5-1")
    assert dear["cost"] == "costlier" and dear["needs_confirmation"] is True
    unknown = ne.plan_model_change(meta, "n_script", "gpt-kie")
    assert unknown["cost"] == "unknown" and unknown["needs_confirmation"] is True
    by_label = ne.plan_model_change(meta, "n_script", "GPT 5.6 Luna")
    assert by_label["after_model_id"] == "gpt-5.6-luna"
    img = ne.plan_model_change(meta, "n_images", "nano-banana-2-lite")
    assert img["kind"] == "image" and img["needs_confirmation"] is False
    assert ne.plan_model_change(meta, "n_images", "flux-2-pro")["needs_confirmation"] is True


def test_model_change_refusals() -> None:
    meta = _meta()
    cases = [("n_script", "nano-banana-2", "model_not_allowed"),
             ("n_images", "gpt-5.6-luna", "model_not_allowed"),
             ("n_script", "no-such-model", "model_not_allowed"),
             ("n_audio", "gpt-5.6-luna", "no_model"),
             (FORCED, "gpt-5.6-luna", "forced_model"),
             ("n_script", "gpt-5.6-sol", "same_model"),
             ("n_missing", "gpt-5.6-luna", "node_not_found")]
    for key, model, code in cases:
        with pytest.raises(ne.NodeEditError) as exc:
            ne.plan_model_change(meta, key, model)
        assert exc.value.code == code, (key, model, exc.value.code)
    assert "Кадры" in str(pytest.raises(ne.NodeEditError, ne.plan_model_change, meta, FORCED,
                                        "gpt-5.6-luna").value)


def test_model_applies_only_where_the_step_reads_it() -> None:
    meta = _meta()
    assert ne.model_state(meta, "n_script")["applies"] is True
    second = ne.model_state(meta, "n_script_2")
    assert second["applies"] is False and "первой ноды" in second["applies_note"]
    assert ne.model_state(meta, "n_fix")["applies"] is True
    plan = ne.plan_model_change(meta, "n_script_2", "gpt-5.6-luna")
    assert plan["applies"] is False and "Внимание" in ne.model_change_text(plan)


@pytest.mark.xfail(strict=True, reason='merge line: int/chat-v1 baseline test, model catalog / prompt library / meta agent of main differ')
def test_model_change_writes_one_node_history_and_pin() -> None:
    meta = _meta()
    new_meta, plan = ne.apply_model_change(meta, "n_script", "gpt-5.6-luna", actor="tester")
    assert _node(new_meta, "n_script")["data"]["modelId"] == "gpt-5.6-luna"
    assert _node(new_meta, "n_plan") == _node(meta, "n_plan")
    assert new_meta["canvas_graph"]["edges"] == meta["canvas_graph"]["edges"]
    assert new_meta[ne.PINS_KEY]["nodes"]["n_script"]["modelId"]["prev"] == "gpt-5.6-sol"
    entry = ne.last_history(new_meta, "n_script", "model")
    assert entry["before"] == {"model_id": "gpt-5.6-sol"}
    assert entry["after"] == {"model_id": "gpt-5.6-luna"}
    rv = ne.plan_model_revert(new_meta, "n_script")
    assert rv["target_model_id"] == "gpt-5.6-sol"
    back, _ = ne.apply_model_change(new_meta, "n_script", rv["target_model_id"])
    assert _node(back, "n_script")["data"]["modelId"] == "gpt-5.6-sol"
    assert "n_script" not in (back.get(ne.PINS_KEY) or {}).get("nodes", {})


def test_revert_refused_when_model_changed_since() -> None:
    new_meta, _ = ne.apply_model_change(_meta(), "n_script", "gpt-5.6-luna")
    _node(new_meta, "n_script")["data"]["modelId"] = "grok-4-6"
    with pytest.raises(ne.NodeEditError) as exc:
        ne.plan_model_revert(new_meta, "n_script")
    assert exc.value.code == "changed_since"
    with pytest.raises(ne.NodeEditError) as exc:
        ne.plan_model_revert(_meta(), "n_script")
    assert exc.value.code == "nothing_to_revert"



def _revert_model(meta: dict, key: str = "n_script") -> tuple[dict, str | None]:
    rv = ne.plan_model_revert(meta, key)
    back, _ = ne.apply_model_change(meta, key, rv["target_model_id"],
                                    revert_of=rv["entry"].get("at"))
    return back, ne.canonical_model_id(ne.explicit_model_id(_node(back, key)))


@pytest.mark.xfail(strict=True, reason='merge line: int/chat-v1 baseline test, model catalog / prompt library / meta agent of main differ')
def test_model_revert_goes_deeper_each_time() -> None:
    meta, _ = ne.apply_model_change(_meta(), "n_script", "gpt-5.6-luna")
    meta, _ = ne.apply_model_change(meta, "n_script", "gpt-5.5")
    meta, now = _revert_model(meta)
    assert now == "gpt-5.6-luna"
    meta, now = _revert_model(meta)
    assert now == "gpt-5.6-sol"
    with pytest.raises(ne.NodeEditError) as exc:
        ne.plan_model_revert(meta, "n_script")
    assert exc.value.code == "nothing_to_revert"
    assert "можно вернуть" not in ne.describe_node({"id": 1, "meta": meta}, "n_script")["text_ru"]


@pytest.mark.xfail(strict=True, reason='merge line: int/chat-v1 baseline test, model catalog / prompt library / meta agent of main differ')
def test_new_model_change_after_revert_starts_from_the_rest_of_the_stack() -> None:
    meta, _ = ne.apply_model_change(_meta(), "n_script", "gpt-5.6-luna")
    meta, _ = ne.apply_model_change(meta, "n_script", "gpt-5.5")
    meta, _ = _revert_model(meta)
    meta, _ = ne.apply_model_change(meta, "n_script", "grok-4-6")
    meta, now = _revert_model(meta)
    assert now == "gpt-5.6-luna"
    meta, now = _revert_model(meta)
    assert now == "gpt-5.6-sol"


def test_legacy_revert_of_a_revert_puts_the_change_back_on_the_stack() -> None:
    meta, _ = ne.apply_model_change(_meta(), "n_script", "gpt-5.6-luna")
    first = ne.last_history(meta, "n_script", "model")
    meta, _ = ne.apply_model_change(meta, "n_script", "gpt-5.6-sol", revert_of=first["at"])
    undo = ne.last_history(meta, "n_script", "model")
    undo["at"] = "2026-01-01T00:00:01+00:00"
    # Старый одноуровневый откат отменял сам откат: правка снова стоит на ноде.
    meta, _ = ne.apply_model_change(meta, "n_script", "gpt-5.6-luna", revert_of=undo["at"])
    rv = ne.plan_model_revert(meta, "n_script")
    assert rv["entry"] is not None and rv["target_model_id"] == "gpt-5.6-sol"


def test_params_revert_goes_deeper_each_time() -> None:
    state = _state()
    meta, _ = ne.apply_params_change(state, "n_review", {"disabled": True})
    meta, _ = ne.apply_params_change({**state, "meta": meta}, "n_review",
                                     {"branches": [{"edge_id": "e3", "kind": "pass"}]})
    meta, out = ne.revert_params({**state, "meta": meta}, "n_review")
    assert [i["param"] for i in out["items"]] == ["branches"]
    assert meta["disabled_nodes"] == ["n_review"]
    meta, out = ne.revert_params({**state, "meta": meta}, "n_review")
    assert [i["param"] for i in out["items"]] == ["disabled"]
    assert meta["disabled_nodes"] == []
    e3 = next(e for e in meta["canvas_graph"]["edges"] if e["id"] == "e3")
    assert e3["data"]["kind"] == "after"
    with pytest.raises(ne.NodeEditError) as exc:
        ne.revert_params({**state, "meta": meta}, "n_review")
    assert exc.value.code == "nothing_to_revert"


def _ui_patch(meta: dict, *, model: str | None = "gpt-5.6-sol", kind: str = "after") -> dict:
    stale = copy.deepcopy(_meta())
    if model is None:
        stale["canvas_graph"]["nodes"][1]["data"].pop("modelId")
    else:
        stale["canvas_graph"]["nodes"][1]["data"]["modelId"] = model
    stale["canvas_graph"]["edges"][2]["data"]["kind"] = kind
    stale["canvas_graph"]["nodes"][0]["position"] = {"x": 99, "y": 1}
    return {"canvas_graph": stale["canvas_graph"], "canvas_pins": {}, "node_edit_history": {}}


@pytest.mark.xfail(strict=True, reason='merge line: int/chat-v1 baseline test, model catalog / prompt library / meta agent of main differ')
def test_stale_canvas_autosave_does_not_undo_the_point_change() -> None:
    base, _ = ne.apply_model_change(_meta(), "n_script", "gpt-5.6-luna")
    merged = merge_project_meta(base, _ui_patch(base), source="test")
    assert _node(merged, "n_script")["data"]["modelId"] == "gpt-5.6-luna"
    assert _node(merged, "n_plan")["position"] == {"x": 99, "y": 1}
    assert merged[ne.PINS_KEY]["nodes"]["n_script"]["modelId"]["value"] == "gpt-5.6-luna"
    assert merged[ne.HISTORY_KEY] == base[ne.HISTORY_KEY]
    synced = merge_project_meta(merged, _ui_patch(merged, model="gpt-5.6-luna"), source="t")
    assert ne.PINS_KEY not in synced
    user = merge_project_meta(merged, _ui_patch(merged, model="grok-4-6"), source="t")
    assert _node(user, "n_script")["data"]["modelId"] == "grok-4-6"
    assert ne.PINS_KEY not in user


@pytest.mark.xfail(strict=True, reason='merge line: int/chat-v1 baseline test, model catalog / prompt library / meta agent of main differ')
def test_stale_autosave_keeps_default_model_choice_and_edge_kind() -> None:
    state = _state()
    base, _ = ne.apply_model_change(state["meta"], "n_script", None)
    assert "modelId" not in _node(base, "n_script")["data"]
    merged = merge_project_meta(base, _ui_patch(base), source="test")
    assert "modelId" not in _node(merged, "n_script")["data"]
    edged, _plan = ne.apply_params_change({**state, "meta": base}, "n_review",
                                          {"branches": [{"edge_id": "e3", "kind": "pass"}]})
    merged = merge_project_meta(edged, _ui_patch(edged, model=None, kind="after"), source="t")
    e3 = next(e for e in merged["canvas_graph"]["edges"] if e["id"] == "e3")
    assert e3["data"]["kind"] == "pass"
    moved = merge_project_meta(merged, _ui_patch(merged, model=None, kind="fail"), source="t")
    e3 = next(e for e in moved["canvas_graph"]["edges"] if e["id"] == "e3")
    assert e3["data"]["kind"] == "fail" and "edges" not in (moved.get(ne.PINS_KEY) or {})



def _items(plan: dict) -> dict:
    return {i["param"]: i for i in plan["items"]}


def test_params_disable_one_node_is_safe_but_type_wide_needs_confirmation() -> None:
    plan = ne.plan_params_change(_state(), "n_review", {"disabled": True})
    assert plan["needs_confirmation"] is False
    assert plan["meta_after"]["disabled_nodes"] == ["n_review"]
    wide = ne.plan_params_change(_state(), "n_script_2", {"disabled": True})
    assert wide["needs_confirmation"] is True
    assert "Закадровый текст" in _items(wide)["disabled"]["note_ru"]
    back = ne.plan_params_change(_state(disabled_nodes=["n_review"]), "n_review",
                                 {"disabled": False})
    assert back["needs_confirmation"] is True
    with pytest.raises(ne.NodeEditError) as exc:
        ne.plan_params_change(_state(disabled_nodes=["n_script"]), "n_script_2",
                              {"disabled": False})
    assert exc.value.code in ("no_effect", "same_value")


def test_params_check_retry_and_prompt_rules() -> None:
    on = ne.plan_params_change(_state(), "n_review", {"check_mode": True})
    assert on["needs_confirmation"] is True
    cfg = on["meta_after"]["excel_gpt_nodes"]["n_review"]
    assert cfg["checkMode"] is True and cfg["emitKinds"] == ["inputs", "reply_txt"]
    assert cfg["role"] == "review"
    with_check = _state(excel_gpt_nodes={"n_review": {"role": "review", "checkMode": True}})
    off = ne.plan_params_change(with_check, "n_review", {"check_mode": False,
                                                         "check_fix": False})
    assert off["needs_confirmation"] is False and set(_items(off)) == {"check_mode", "check_fix"}
    with pytest.raises(ne.NodeEditError) as exc:
        ne.plan_params_change(_state(), "n_script", {"check_mode": True})
    assert exc.value.code == "not_applicable"
    fast = ne.plan_params_change(_state(), "n_script", {"retry_profile": "fast"})
    assert fast["needs_confirmation"] is False
    assert fast["meta_after"]["step_failure_profile"] == "fast"
    slow = ne.plan_params_change(_state(step_failure_profile="fast"), "n_script",
                                 {"retry_profile": "safe"})
    assert slow["needs_confirmation"] is True
    with pytest.raises(ne.NodeEditError) as exc:
        ne.plan_params_change(_state(), "n_script", {"prompt_variant": "nope"})
    assert exc.value.code in ("variant_not_found", "not_applicable")
    with pytest.raises(ne.NodeEditError) as exc:
        ne.plan_params_change(_state(), "n_script", {})
    assert exc.value.code == "no_changes"


def test_prompt_variant_must_exist_and_take_effect(prompts_root) -> None:
    plan = ne.plan_params_change(_state(), "n_plan", {"prompt_variant": "plan_alt"})
    assert plan["needs_confirmation"] is False
    assert plan["meta_after"]["prompt_slot_variants"]["n_plan"]["main"] == "plan_alt"
    shadowed = {**_state(), "prompt_overrides": {"plan": "default"}}
    with pytest.raises(ne.NodeEditError) as exc:
        ne.plan_params_change(shadowed, "n_plan", {"prompt_variant": "plan_alt"})
    assert exc.value.code == "no_effect"


def test_branches_only_for_verdict_roles_and_costly_kinds_need_confirmation() -> None:
    safe = ne.plan_params_change(_state(), "n_review",
                                 {"branches": [{"edge_id": "e3", "kind": "pass"}]})
    assert safe["needs_confirmation"] is False
    edge = next(e for e in safe["meta_after"]["canvas_graph"]["edges"] if e["id"] == "e3")
    assert edge["data"]["kind"] == "pass"
    risky = ne.plan_params_change(_state(), "n_review",
                                  {"branches": [{"edge_id": "e3", "kind": "fail"}]})
    assert risky["needs_confirmation"] is True
    with pytest.raises(ne.NodeEditError) as exc:
        ne.plan_params_change(_state(), "n_fix", {"branches": [{"edge_id": "e4", "kind": "pass"}]})
    assert exc.value.code == "not_applicable"
    with pytest.raises(ne.NodeEditError) as exc:
        ne.plan_params_change(_state(), "n_review",
                              {"branches": [{"edge_id": "e1", "kind": "pass"}]})
    assert exc.value.code == "edge_not_found"


def test_params_revert_restores_exact_values_and_respects_later_edits() -> None:
    state = _state(excel_gpt_nodes={"n_review": {"role": "review", "gateStatus": "ok"}})
    meta1, _ = ne.apply_params_change(state, "n_review",
                                      {"check_mode": True, "disabled": True,
                                       "branches": [{"edge_id": "e3", "kind": "pass"}]})
    meta1["excel_gpt_nodes"]["n_review"]["gateStatus"] = "fail"
    meta2, out = ne.revert_params({**state, "meta": meta1}, "n_review")
    cfg = meta2["excel_gpt_nodes"]["n_review"]
    assert "checkMode" not in cfg and cfg["gateStatus"] == "fail"
    assert meta2["disabled_nodes"] == []
    e3 = next(e for e in meta2["canvas_graph"]["edges"] if e["id"] == "e3")
    assert e3["data"]["kind"] == "after"
    assert {i["param"] for i in out["items"]} == {"check_mode", "disabled", "branches"}
    meta1["disabled_nodes"] = ["n_review", "n_fix"]
    with pytest.raises(ne.NodeEditError) as exc:
        ne.revert_params({**state, "meta": meta1}, "n_review")
    assert exc.value.code == "changed_since"


@pytest.mark.xfail(strict=True, reason='merge line: int/chat-v1 baseline test, model catalog / prompt library / meta agent of main differ')
def test_describe_is_russian_and_honest() -> None:
    d = ne.describe_node(_state(), "n_script_2")
    assert "Модель: GPT 5.6 Sol (по умолчанию)" in d["text_ru"]
    assert "первой ноды" in d["text_ru"]
    forced = ne.describe_node(_state(), FORCED)["text_ru"]
    assert "не учитывается" in forced
    review = ne.describe_node(_state(), "n_review")["text_ru"]
    assert "Роль: review" in review and "Связь к «Правка»: обычная связь" in review
    assert "_" not in d["text_ru"].replace("n_script_2", "")



async def _project_with_meta(env, meta: dict, status=ProjectStatus.new) -> int:
    async with env.factory() as s:
        p = Project(topic="t", slug=f"ne-{env.tmp_path.name[-6:]}-{len(meta)}", status=status,
                    meta=meta, prompt_overrides={})
        s.add(p)
        await s.commit()
        return p.id


async def _meta_of(env, pid: int) -> dict:
    async with env.factory() as s:
        return dict((await s.get(Project, pid)).meta or {})


async def _plan(env, steps):
    r = await env.client.post(f"{AL}/plans/preview", json={"steps": steps})
    return r


async def _run(env, steps, *, approve: bool = False) -> dict:
    r = await _plan(env, steps)
    assert r.status_code == 200, r.text
    plan_id = r.json()["plan_id"]
    if approve:
        a = await env.client.post(f"{AL}/plans/{plan_id}/approve")
        assert a.status_code == 200, a.text
    run = await env.client.post(f"{AL}/plans/{plan_id}/run")
    assert run.status_code == 200, run.text
    await executor.wait(plan_id)
    return (await env.client.get(f"{AL}/plans/{plan_id}")).json()


def test_catalog_tiers() -> None:
    expect = {"node.describe": "read", "node.list_models": "read",
              "node.set_model": "local_safe", "node.set_model_costly": "destructive",
              "node.revert_model": "local_safe", "node.set_params": "local_safe",
              "node.set_params_costly": "destructive", "node.revert_params": "local_safe",
              "prompt.edit_text": "local_safe"}
    for aid, risk in expect.items():
        spec = registry.get(aid)
        assert spec.risk == risk, aid
        assert spec.spends_money is False, aid
        assert spec.needs_approval is (risk == "destructive"), aid


@pytest.mark.xfail(strict=True, reason='merge line: int/chat-v1 baseline test, model catalog / prompt library / meta agent of main differ')
async def test_set_model_through_the_layer_and_revert(env) -> None:
    pid = await _project_with_meta(env, _meta())
    params = {"project_id": pid, "node_key": "n_script", "model_id": "gpt-5.6-luna"}
    plan = await _run(env, [{"action_id": "node.set_model", "params": params}])
    assert plan["status"] == "succeeded", plan
    assert plan["needs_approval"] is False
    result = plan["steps"][0]["result"]
    assert result["after_model_id"] == "gpt-5.6-luna" and "дешевле" in result["text_ru"]
    meta = await _meta_of(env, pid)
    assert _node(meta, "n_script")["data"]["modelId"] == "gpt-5.6-luna"
    r = await env.client.patch(f"/api/projects/{pid}", json={"meta": _ui_patch(meta)})
    assert r.status_code == 200, r.text
    meta = await _meta_of(env, pid)
    assert _node(meta, "n_script")["data"]["modelId"] == "gpt-5.6-luna"
    desc = await _run(env, [{"action_id": "node.get_config",
                             "params": {"project_id": pid, "node_key": "n_script"}}])
    assert "GPT 5.6 Luna" in desc["steps"][0]["result"]["text_ru"]
    back = await _run(env, [{"action_id": "node.revert_model",
                             "params": {"project_id": pid, "node_key": "n_script"}}])
    assert back["status"] == "succeeded", back
    assert _node(await _meta_of(env, pid), "n_script")["data"]["modelId"] == "gpt-5.6-sol"


@pytest.mark.xfail(strict=True, reason='merge line: int/chat-v1 baseline test, model catalog / prompt library / meta agent of main differ')
async def test_two_reverts_in_a_row_through_the_layer_go_deeper(env) -> None:
    pid = await _project_with_meta(env, _meta())
    for model in ("gpt-5.5", "gpt-5.6-luna"):
        plan = await _run(env, [{"action_id": "node.set_model", "params": {
            "project_id": pid, "node_key": "n_script", "model_id": model}}])
        assert plan["status"] == "succeeded", plan
    revert = [{"action_id": "node.revert_model",
               "params": {"project_id": pid, "node_key": "n_script"}}]
    assert (await _run(env, revert))["status"] == "succeeded"
    assert _node(await _meta_of(env, pid), "n_script")["data"]["modelId"] == "gpt-5.5"
    assert (await _run(env, revert))["status"] == "succeeded"
    assert _node(await _meta_of(env, pid), "n_script")["data"]["modelId"] == "gpt-5.6-sol"
    r = await _plan(env, revert)
    assert r.status_code == 422 and "уже возвращены" in r.text


async def test_costlier_model_needs_a_human(env) -> None:
    pid = await _project_with_meta(env, _meta())
    params = {"project_id": pid, "node_key": "n_script", "model_id": "claude-fable-5-1"}
    r = await _plan(env, [{"action_id": "node.set_model", "params": params}])
    assert r.status_code == 422 and "подтверждение" in r.text
    r = await env.client.patch(f"/api/projects/{pid}/nodes/n_script/model",
                               json={"model_id": "claude-fable-5-1"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "needs_confirmation"
    r = await _plan(env, [{"action_id": "node.set_model_costly", "params": params}])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["preview"]["needs_approval"] is True
    assert "дороже" in body["preview"]["destructive_effects"][0]["text"]
    run = await env.client.post(f"{AL}/plans/{body['plan_id']}/run")
    assert run.status_code == 403
    assert _node(await _meta_of(env, pid), "n_script")["data"]["modelId"] == "gpt-5.6-sol"
    plan = await _run(env, [{"action_id": "node.set_model_costly", "params": params}],
                      approve=True)
    assert plan["status"] == "succeeded", plan
    assert _node(await _meta_of(env, pid), "n_script")["data"]["modelId"] == "claude-fable-5-1"


async def test_busy_project_is_refused(env) -> None:
    pid = await _project_with_meta(env, _meta(), status=ProjectStatus.scripting)
    r = await env.client.patch(f"/api/projects/{pid}/nodes/n_script/model",
                               json={"model_id": "gpt-5.6-luna"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "project_busy"
    r = await _plan(env, [{"action_id": "node.set_model", "params": {
        "project_id": pid, "node_key": "n_script", "model_id": "gpt-5.6-luna"}}])
    assert r.status_code == 422


async def test_set_params_safe_and_costly_and_revert(env) -> None:
    pid = await _project_with_meta(env, _meta())
    safe = {"project_id": pid, "node_key": "n_review", "disabled": True,
            "branches": [{"edge_id": "e3", "kind": "pass"}]}
    plan = await _run(env, [{"action_id": "node.set_params", "params": safe}])
    assert plan["status"] == "succeeded", plan
    meta = await _meta_of(env, pid)
    assert meta["disabled_nodes"] == ["n_review"]
    costly = {"project_id": pid, "node_key": "n_review", "check_mode": True}
    r = await _plan(env, [{"action_id": "node.set_params", "params": costly}])
    assert r.status_code == 422
    plan = await _run(env, [{"action_id": "node.set_params_costly", "params": costly}],
                      approve=True)
    assert plan["status"] == "succeeded", plan
    assert (await _meta_of(env, pid))["excel_gpt_nodes"]["n_review"]["checkMode"] is True
    back = await _run(env, [{"action_id": "node.revert_params",
                             "params": {"project_id": pid, "node_key": "n_review"}}])
    assert back["status"] == "succeeded", back
    assert "checkMode" not in (await _meta_of(env, pid))["excel_gpt_nodes"]["n_review"]


@pytest.mark.xfail(strict=True, reason='merge line: int/chat-v1 baseline test, model catalog / prompt library / meta agent of main differ')
async def test_model_options_list(env) -> None:
    pid = await _project_with_meta(env, _meta())
    r = await env.client.get(f"/api/projects/{pid}/nodes/n_images/model-options")
    assert r.status_code == 200, r.text
    opts = {o["id"]: o for o in r.json()["options"]}
    assert opts["nano-banana-2-lite"]["needs_confirmation"] is False
    assert opts["flux-2-pro"]["needs_confirmation"] is True
    assert all(o["id"] not in ("gpt-5.6-sol",) for o in r.json()["options"])



async def test_cc_t2_now_covers_image_prompt_nodes(cc, prompts_root) -> None:
    (prompts_root / "05_image_prompts").mkdir(parents=True, exist_ok=True)
    (prompts_root / "05_image_prompts" / "default.md").write_text(
        "# Image prompts\nWrite one image prompt per frame. Keep the style line short.",
        encoding="utf-8")
    cc.gw.state["meta"]["canvas_graph"]["nodes"].append(
        {"id": "n_image_prompts", "type": "image_prompts", "data": {}})
    cc.gw.state["meta"]["canvas_graph"]["nodes"].append(
        {"id": "node_custom", "type": "image_prompts", "data": {}})
    text = ("# Image prompts\nWrite one image prompt per frame. Keep the style line short and "
            "cinematic.")
    view = await cc_service.propose(cc.gw, tier="T2", kind="prompt_text", project_id=1,
                                    node_key="n_image_prompts", rationale="тест",
                                    proposer=HUMAN, candidate={"text": text})
    assert view["status"] == "proposed" and view["candidate"]["variant"].startswith("cc-")
    with pytest.raises(ChangeRefused) as exc:
        await cc_service.propose(cc.gw, tier="T2", kind="prompt_text", project_id=1,
                                 node_key="node_custom", rationale="тест", proposer=HUMAN,
                                 candidate={"text": text})
    assert exc.value.code == "lint_failed" and "not take effect" in str(exc.value)
    with pytest.raises(ChangeRefused) as exc:
        await cc_service.propose(cc.gw, tier="T2", kind="prompt_text", project_id=1,
                                 node_key="n_hero", rationale="тест", proposer=HUMAN,
                                 candidate={"text": text})
    assert exc.value.code == "node_type_not_allowed" and "запасным" in str(exc.value)


@pytest.fixture
async def ccenv_nodes(env, prompts_root):
    async with env.engine.begin() as conn:
        await cs.ensure_change_control_schema(conn)
    env.pid = await _project_with_meta(env, _meta())
    return env


async def test_prompt_edit_text_creates_a_proposal_only(ccenv_nodes, prompts_root) -> None:
    env = ccenv_nodes
    before = (prompts_root / "01_plan" / "default.md").read_bytes()
    params = {"project_id": env.pid, "node_key": "n_plan", "rationale": "короче",
              "edits": [{"find": "Write a plan", "replace": "Write a short plan"}]}
    plan = await _run(env, [{"action_id": "prompt.edit_text", "params": params}])
    assert plan["status"] == "succeeded", plan
    assert plan["needs_approval"] is False
    res = plan["steps"][0]["result"]
    assert res["apply_step"]["action_id"] == "change.apply"
    assert "+Topic" in res["proposal_diff"] or "short plan" in res["proposal_diff"]
    from app.orchestrator.contracts import r21_result_problem
    assert r21_result_problem({"status": "ok", "result": res}) is None
    assert (prompts_root / "01_plan" / "default.md").read_bytes() == before
    assert "prompt_slot_variants" not in await _meta_of(env, env.pid)
    bad = dict(params, edits=[{"find": "нет такого фрагмента", "replace": "x"}])
    r = await _plan(env, [{"action_id": "prompt.edit_text", "params": bad}])
    assert r.status_code == 422 and "не найден" in r.text
    both = dict(params, text="x" * 50)
    r = await _plan(env, [{"action_id": "prompt.edit_text", "params": both}])
    assert r.status_code == 422
    applied = await _run(env, [res["apply_step"]], approve=True)
    assert applied["status"] == "succeeded", applied
    meta = await _meta_of(env, env.pid)
    assert meta["prompt_slot_variants"]["n_plan"]["main"].startswith("cc-")


async def test_prompt_edit_text_append_adds_requirement_at_the_end(ccenv_nodes, prompts_root) -> None:
    from app.services.action_layer.spec import ensure_builtin_actions, registry
    env = ccenv_nodes
    before = (prompts_root / "01_plan" / "default.md").read_text(encoding="utf-8")
    params = {"project_id": env.pid, "node_key": "n_plan", "rationale": "короче",
              "append": "Пиши короче, не больше 5 предложений."}
    plan = await _run(env, [{"action_id": "prompt.edit_text", "params": params}])
    assert plan["status"] == "succeeded", plan
    res = plan["steps"][0]["result"]
    assert res["added_lines"] == ["Пиши короче, не больше 5 предложений."]
    assert res["removed_lines"] == []
    text = res["text_ru"]
    assert "Добавлено:\n+ Пиши короче, не больше 5 предложений." in text
    assert "напишите «примени правку промта»" in text
    assert "«Регистратор» → «Контролируемые изменения»" in text
    assert "Убрано" not in text
    assert (prompts_root / "01_plan" / "default.md").read_text(encoding="utf-8") == before
    both = dict(params, edits=[{"find": "Write a plan", "replace": "x"}])
    r = await _plan(env, [{"action_id": "prompt.edit_text", "params": both}])
    assert r.status_code == 422
    ensure_builtin_actions()
    assert registry.find("change.apply").needs_approval is True
    applied = await _run(env, [res["apply_step"]], approve=True)
    assert applied["status"] == "succeeded", applied


async def test_prompt_append_real_lint_path_apply_and_secret_refusal(ccenv_nodes, prompts_root) -> None:
    """prompt.edit_text -> change_control propose/validate -> lints (no mocks) -> change.apply.

    Live stand crashed here with ImportError in lints.new_secret_hits; the two tests above were
    hidden behind a strict xfail, so nothing caught it. This one has no xfail and asserts both that
    the real lint runs (a credential-like append is refused by it) and that a plain append applies.
    """
    from app.services.change_control import lints

    assert lints.new_secret_hits("Пиши коротко.", "") == []
    assert lints.new_secret_hits("api_key=abcdefgh12345678", "") != []
    env = ccenv_nodes
    base = {"project_id": env.pid, "node_key": "n_plan", "rationale": "короче"}
    secret = dict(base, append="token: abcdefgh12345678abcdefgh")
    r = await _plan(env, [{"action_id": "prompt.edit_text", "params": secret}])
    assert r.status_code == 422, r.text
    assert "credential-like" in r.text and "ImportError" not in r.text
    plan = await _run(env, [{"action_id": "prompt.edit_text", "params": dict(base, append="Пиши коротко.")}])
    assert plan["status"] == "succeeded", plan
    res = plan["steps"][0]["result"]
    assert res["added_lines"] == ["Пиши коротко."]
    applied = await _run(env, [res["apply_step"]], approve=True)
    assert applied["status"] == "succeeded", applied
    variant = (await _meta_of(env, env.pid))["prompt_slot_variants"]["n_plan"]["main"]
    assert variant.startswith("cc-")
    files = list((prompts_root / "01_plan").glob(f"{variant}*.md"))
    assert files and files[0].read_text(encoding="utf-8").rstrip().endswith("Пиши коротко.")


def test_prompt_edit_summary_lines() -> None:
    from app.services.action_layer.builtin_node_edit import changed_lines, prompt_edit_text_ru
    added, removed = changed_lines("a\nb\nc", "a\nB\nc\nd")
    assert added == ["B", "d"] and removed == ["b"]
    text = prompt_edit_text_ru("n_plan", 5, 8, added, removed)
    assert "Было: 5 символов, стало: 8." in text
    assert "Добавлено:\n+ B\n+ d" in text and "Убрано:\n− b" in text



class _FakeGpt:
    def __init__(self, reply: str) -> None:
        self._reply = reply

    async def ask_fresh(self, text: str, **kw) -> str:
        return self._reply


async def _chat(env, monkeypatch, pid: int, actions: list) -> dict:
    reply = json.dumps({"actions": actions}, ensure_ascii=False)
    monkeypatch.setattr("app.services.gpt_client.get_gpt_client", lambda: _FakeGpt(reply))
    r = await env.client.post(f"/api/db/projects/{pid}/orchestrator/chat",
                              json={"message": "сделай", "history": []})
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.xfail(strict=True, reason='merge line: int/chat-v1 baseline test, model catalog / prompt library / meta agent of main differ')
async def test_chat_set_text_llm_tells_which_nodes_are_affected(env, monkeypatch) -> None:
    pid = await _project_with_meta(env, _meta())
    data = await _chat(env, monkeypatch, pid, [{"set_text_llm": {"provider": "vibecode",
                                                                 "model_id": "claude-sonnet-5"}}])
    assert data["error"] is None
    assert "модели нод не менялись" in data["actions_run"][0]["set_text_llm"]
    assert "НЕ влияет" in data["reply"] and "«Сценарий» — GPT 5.6 Sol" in data["reply"]
    assert "Кадры QC" in data["reply"]
    assert _node(await _meta_of(env, pid), "n_script")["data"]["modelId"] == "gpt-5.6-sol"


@pytest.mark.xfail(strict=True, reason='merge line: int/chat-v1 baseline test, model catalog / prompt library / meta agent of main differ')
async def test_chat_set_node_model_cheap_applies_costly_waits(env, monkeypatch) -> None:
    pid = await _project_with_meta(env, _meta())
    data = await _chat(env, monkeypatch, pid, [{"set_node_model": {
        "node_key": "Закадровый текст", "model_id": "GPT 5.6 Luna"}}])
    assert data["error"] is None, data
    assert "set_node_model" in data["actions_run"][0]
    assert _node(await _meta_of(env, pid), "n_script")["data"]["modelId"] == "gpt-5.6-luna"
    data = await _chat(env, monkeypatch, pid, [{"set_node_model": {
        "node_key": "n_script", "model_id": "claude-fable-5-1"}}])
    assert data["pending_confirm"][0]["kind"] == "set_node_model"
    assert "подтверждение" in data["reply"]
    assert _node(await _meta_of(env, pid), "n_script")["data"]["modelId"] == "gpt-5.6-luna"
    r = await env.client.post(f"/api/db/projects/{pid}/orchestrator/confirm-set-node-model",
                              json={"node_key": "n_script", "model_id": "claude-fable-5-1"})
    assert r.status_code == 200, r.text
    assert _node(await _meta_of(env, pid), "n_script")["data"]["modelId"] == "claude-fable-5-1"
    data = await _chat(env, monkeypatch, pid, [{"revert_node_model": {"node_key": "n_script"}}])
    assert data["error"] is None, data
    assert _node(await _meta_of(env, pid), "n_script")["data"]["modelId"] == "gpt-5.6-luna"
    data = await _chat(env, monkeypatch, pid, [{"set_node_model": {
        "node_key": FORCED, "model_id": "gpt-5.6-luna"}}])
    assert "Кадры" in (data["error"] or "")


@pytest.mark.xfail(strict=True, reason='merge line: int/chat-v1 baseline test, model catalog / prompt library / meta agent of main differ')
async def test_meta_agent_save_keeps_the_previous_text_in_history(env, prompts_root) -> None:
    from app.services.prompt_history import list_prompt_versions
    body = {"step_code": "plan", "name": "meta_x", "content": "первый текст промта для плана",
            "activate": False}
    r = await env.client.post("/api/meta-agent/save-and-activate", json=body)
    assert r.status_code == 200, r.text
    r = await env.client.post("/api/meta-agent/save-and-activate",
                              json={**body, "content": "второй текст промта для плана"})
    assert r.status_code == 200, r.text
    assert (prompts_root / "01_plan" / "meta_x.md").read_text(encoding="utf-8").startswith("второй")
    versions = list_prompt_versions("plan", "meta_x")
    assert len(versions) == 1


def test_list_models_text_names_cheaper_options() -> None:
    from app.services.action_layer.builtin_node_edit import _models_text_ru

    text = _models_text_ru({
        "current": {"label": "Сценарий", "model_label": "GPT 5.6 Sol", "price_ru": "$1 за 1 млн"},
        "options": [{"id": "gpt-5.6-luna", "label": "GPT 5.6 Luna", "cost_vs_current": "cheaper",
                     "price_ru": "$0.4 за 1 млн", "needs_confirmation": False}]})
    assert "GPT 5.6 Sol" in text and "GPT 5.6 Luna" in text and "дешевле" in text
    assert "нет" in _models_text_ru({"current": {"label": "Сценарий"}, "options": []})
