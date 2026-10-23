from __future__ import annotations

import pytest

from app.models import Project, ProjectStatus
from app.orchestrator.agents.summarizer import known_text
from app.services.action_layer import builtin_node_edit as bne
from app.services.action_layer.context import ActionCallError
from app.services.action_layer.spec import ensure_builtin_actions, registry
from tests.action_layer_support import env  # noqa: F401
from tests.change_control_support import prompts_root  # noqa: F401
from tests.test_action_layer_node_edit import AL, _meta, _meta_of, _node, _plan, _run

MANY = ("node.set_model_many", "node.set_prompt_variant_many")


@pytest.fixture(autouse=True)
def _builtin():
    ensure_builtin_actions()


_SEQ = iter(range(1, 1000))


async def _project(env, status=ProjectStatus.new, meta: dict | None = None) -> int:  # noqa: F811
    async with env.factory() as s:
        p = Project(topic="t", slug=f"many-{next(_SEQ)}", status=status,
                    meta=meta or _meta(), prompt_overrides={})
        s.add(p)
        await s.commit()
        return p.id


async def _projects(env, *statuses) -> list[int]:  # noqa: F811
    return [await _project(env, status) for status in statuses]


def test_many_actions_need_one_confirmation_and_spend_nothing() -> None:
    for aid in MANY:
        spec = registry.get(aid)
        assert spec.risk == "destructive" and spec.needs_approval, aid
        assert spec.spends_money is False and spec.reversible, aid
        assert not any(w in aid for w in ("delete", "remove", "drop", "purge", "clear", "wipe")), aid


@pytest.mark.xfail(strict=True, reason='merge line: model catalog of WebVideoPipeline main differs from int/chat-v1 (default text model, model ids); prompt library of main')
async def test_set_model_many_changes_free_projects_and_reports_the_rest(env) -> None:  # noqa: F811
    free_a, busy, free_b = await _projects(env, ProjectStatus.new, ProjectStatus.scripting,
                                           ProjectStatus.new)
    meta = _meta()
    _node(meta, "n_script")["data"]["modelId"] = "gpt-5.6-luna"
    same = await _project(env, meta=meta)
    params = {"project_ids": [free_a, busy, free_b, same], "node_key": "n_script",
              "model_id": "gpt-5.6-luna"}
    r = await _plan(env, [{"action_id": "node.set_model_many", "params": params}])
    assert r.status_code == 200, r.text
    preview = r.json()["preview"]
    assert preview["needs_approval"] is True
    assert f"#{free_a}" in preview["destructive_effects"][0]["text"]
    run = await env.client.post(f"{AL}/plans/{r.json()['plan_id']}/run")
    assert run.status_code == 403

    plan = await _run(env, [{"action_id": "node.set_model_many", "params": params}], approve=True)
    assert plan["status"] == "succeeded", plan
    result = plan["steps"][0]["result"]
    assert [c["project_id"] for c in result["changed"]] == [free_a, free_b]
    skipped = {s["project_id"]: s["reason"] for s in result["skipped"]}
    assert set(skipped) == {busy, same}
    assert "выполняется шаг" in skipped[busy] and "уже стоит" in skipped[same]
    for pid in (free_a, free_b):
        assert _node(await _meta_of(env, pid), "n_script")["data"]["modelId"] == "gpt-5.6-luna"
    assert _node(await _meta_of(env, busy), "n_script")["data"]["modelId"] == "gpt-5.6-sol"
    text = known_text("node.set_model_many", result)
    assert f"#{free_a} (была «GPT 5.6 Sol»)" in text and "в 2 проектах" in text
    assert f"#{busy} - " in text

    back = await _run(env, [{"action_id": "node.revert_model",
                             "params": {"project_id": free_a, "node_key": "n_script"}}])
    assert back["status"] == "succeeded", back
    assert _node(await _meta_of(env, free_a), "n_script")["data"]["modelId"] == "gpt-5.6-sol"
    assert _node(await _meta_of(env, free_b), "n_script")["data"]["modelId"] == "gpt-5.6-luna"


async def test_revert_model_many_returns_each_project_and_names_it(env) -> None:  # noqa: F811
    first, second, untouched = await _projects(env, ProjectStatus.new, ProjectStatus.new,
                                               ProjectStatus.new)
    params = {"project_ids": [first, second], "node_key": "n_script", "model_id": "gpt-5.6-luna"}
    plan = await _run(env, [{"action_id": "node.set_model_many", "params": params}], approve=True)
    assert plan["status"] == "succeeded", plan

    back = await _run(env, [{"action_id": "node.revert_model_many", "params": {
        "project_ids": [first, second, untouched], "node_key": "n_script"}}])
    assert back["status"] == "succeeded", back
    result = back["steps"][0]["result"]
    assert [c["project_id"] for c in result["changed"]] == [first, second]
    assert [s["project_id"] for s in result["skipped"]] == [untouched]
    for pid in (first, second):
        assert _node(await _meta_of(env, pid), "n_script")["data"]["modelId"] == "gpt-5.6-sol"
    text = known_text("node.revert_model_many", result)
    assert "Вернул прежнюю модель в 2 проектах" in text
    assert f"**#{first}** — " in text and f"**#{second}** — " in text and f"#{untouched} - " in text


async def test_set_model_many_refused_when_no_project_fits(env) -> None:  # noqa: F811
    busy = await _projects(env, ProjectStatus.scripting, ProjectStatus.scripting)
    r = await _plan(env, [{"action_id": "node.set_model_many", "params": {
        "project_ids": busy, "node_key": "n_script", "model_id": "gpt-5.6-luna"}}])
    assert r.status_code == 422 and "ни в одном проекте" in r.text
    r = await _plan(env, [{"action_id": "node.set_model_many", "params": {
        "project_ids": [busy[0], busy[0]], "node_key": "n_script", "model_id": "gpt-5.6-luna"}}])
    assert r.status_code == 422 and "повторы" in r.text


@pytest.mark.xfail(strict=True, reason='merge line: model catalog of WebVideoPipeline main differs from int/chat-v1 (default text model, model ids); prompt library of main')
async def test_set_prompt_variant_many_snapshots_each_project(env, prompts_root) -> None:  # noqa: F811
    first, busy, second = await _projects(env, ProjectStatus.new, ProjectStatus.scripting,
                                          ProjectStatus.new)
    params = {"project_ids": [first, busy, second], "node_key": "n_plan", "variant": "plan_alt"}
    plan = await _run(env, [{"action_id": "node.set_prompt_variant_many", "params": params}],
                      approve=True)
    assert plan["status"] == "succeeded", plan
    result = plan["steps"][0]["result"]
    changed = {c["project_id"]: c for c in result["changed"]}
    assert set(changed) == {first, second}
    assert [s["project_id"] for s in result["skipped"]] == [busy]
    assert changed[first]["snapshot_id"] != changed[second]["snapshot_id"]
    assert all(c["rollback_possible"] for c in changed.values())
    for pid in (first, second):
        assert (await _meta_of(env, pid))["prompt_slot_variants"]["n_plan"]["main"] == "plan_alt"
    assert "prompt_slot_variants" not in await _meta_of(env, busy)
    assert "Поменял промт ноды в 2 проектах" in known_text("node.set_prompt_variant_many", result)

    back = await _run(env, [{"action_id": "history.rollback",
                             "params": {"snapshot_id": changed[first]["snapshot_id"]}}],
                      approve=True)
    assert back["status"] == "succeeded", back
    assert ((await _meta_of(env, first)).get("prompt_slot_variants") or {}).get(
        "n_plan", {}).get("main") is None
    assert (await _meta_of(env, second))["prompt_slot_variants"]["n_plan"]["main"] == "plan_alt"


async def test_one_project_failing_mid_run_does_not_stop_the_rest() -> None:
    async def check(_ctx, _one):
        return None

    async def apply(_ctx, one):
        if one["project_id"] == 11:
            raise ActionCallError(409, {"code": "project_busy",
                                        "message": "В проекте сейчас выполняется шаг."})
        return {"text_ru": "ok"}

    out = await bne._run_many(None, {"project_ids": [10, 11, 12], "node_key": "n", "variant": "v"},
                              "variant", check, apply)
    assert [c["project_id"] for c in out["changed"]] == [10, 12]
    assert out["skipped"] == [{"project_id": 11, "reason": "В проекте сейчас выполняется шаг"}]
    assert out["count"] == 2


def test_planner_catalog_offers_many_actions_for_a_batch_request() -> None:
    from app.orchestrator.agents.action_catalog import select_ids

    specs = registry.list()
    ids = select_ids("во всех проектах 10-14 поставь модель GPT 5.6 Luna на ноду сценария", specs, 24)
    assert "node.set_model_many" in ids
    ids = select_ids("во всех проектах 10-14 поставь на ноду проверки промт plan_alt", specs, 24)
    assert "node.set_prompt_variant_many" in ids
    ids = select_ids("в проектах 10, 12 и 14 верни прежнюю модель на ноде промты картинок", specs, 24)
    assert "node.revert_model_many" in ids
