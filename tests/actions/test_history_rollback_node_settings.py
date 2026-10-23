"""Откат по журналу для модели и настроек ноды и настроек проекта (history.rollback)."""

from __future__ import annotations

import copy

import pytest

from app.models import Project
from app.services import node_edit as ne
from app.services.action_layer import executor, store
from app.services.action_layer.spec import ensure_builtin_actions
from tests.action_layer_support import env

BASE = "/api/action-layer"

NODES = [
    {"id": "n_plan", "type": "plan", "data": {"label": "Сценарий"}},
    {"id": "n_script", "type": "script", "data": {"label": "Закадровый текст",
                                                  "modelId": "gpt-5.6-sol"}},
    {"id": "n_review", "type": "excel_gpt", "data": {"label": "Проверка", "slotIndex": 1}},
    {"id": "n_fix", "type": "excel_gpt", "data": {"label": "Правка", "slotIndex": 2}},
]
EDGES = [
    {"id": "e1", "source": "n_plan", "target": "n_script"},
    {"id": "e2", "source": "n_script", "target": "n_review"},
    {"id": "e3", "source": "n_review", "target": "n_fix", "data": {"kind": "after"}},
]


@pytest.fixture(autouse=True)
def _builtin():
    ensure_builtin_actions()


@pytest.fixture(autouse=True)
def _no_env_profile(monkeypatch):
    monkeypatch.delenv("STEP_FAILURE_WAIT_PROFILE", raising=False)


def step(action_id, /, **params):
    return {"action_id": action_id, "params": params}


async def _project(env) -> int:
    pid = await env.add_project(frames=1)
    async with env.factory() as s:
        project = await s.get(Project, pid)
        project.meta = {"canvas_graph": {"workflow_id": 1, "nodes": copy.deepcopy(NODES),
                                         "edges": copy.deepcopy(EDGES)},
                        "excel_gpt_nodes": {"n_review": {"role": "review"}}}
        await s.commit()
    return pid


async def _run(env, steps, *, approve=False) -> dict:
    r = await env.client.post(f"{BASE}/plans/preview", json={"steps": steps})
    assert r.status_code == 200, r.text
    plan = r.json()["plan_id"]
    if approve:
        assert (await env.client.post(f"{BASE}/plans/{plan}/approve")).status_code == 200
    assert (await env.client.post(f"{BASE}/plans/{plan}/run")).status_code == 200
    await executor.wait(plan)
    return (await env.client.get(f"{BASE}/plans/{plan}")).json()


async def _result(env, steps, *, approve=False) -> dict:
    view = await _run(env, steps, approve=approve)
    assert view["status"] == "succeeded", [(s.get("error"), s.get("status")) for s in view["steps"]]
    return view["steps"][-1]["result"]


async def _meta(env, pid) -> dict:
    async with env.factory() as s:
        return dict((await s.get(Project, pid)).meta or {})


def _model(meta: dict) -> str | None:
    return ne.explicit_model_id(ne.find_node(meta, "n_script"))


@pytest.mark.xfail(strict=True, reason='merge line: model catalog of WebVideoPipeline main differs from int/chat-v1 (default text model, model ids)')
async def test_model_change_rolls_back_via_history_and_keeps_the_node_stack(env) -> None:
    pid = await _project(env)
    first = await _result(env, [step("node.set_model", project_id=pid, node_key="n_script",
                                     model_id="gpt-5.5")])
    second = await _result(env, [step("node.set_model", project_id=pid, node_key="n_script",
                                      model_id="gpt-5.6-luna")])
    assert second["rollback_possible"] is True
    snap = await store.get_snapshot(second["snapshot_id"])
    assert (snap.kind, snap.before, snap.after) == ("node_model", "gpt-5.5", "gpt-5.6-luna")
    assert (await store.get_snapshot(first["snapshot_id"])).before == "gpt-5.6-sol"

    back = await _result(env, [step("history.rollback", snapshot_id=second["snapshot_id"])],
                         approve=True)
    assert _model(await _meta(env, pid)) == "gpt-5.5"
    assert "вернул модель «GPT 5.5»" in back["text_ru"]
    # Откат по журналу снял правку со стека ноды: следующий возврат идёт глубже.
    await _result(env, [step("node.revert_model", project_id=pid, node_key="n_script")])
    assert _model(await _meta(env, pid)) == "gpt-5.6-sol"


@pytest.mark.xfail(strict=True, reason='merge line: model catalog of WebVideoPipeline main differs from int/chat-v1 (default text model, model ids)')
async def test_model_rollback_refused_when_changed_since(env) -> None:
    pid = await _project(env)
    changed = await _result(env, [step("node.set_model", project_id=pid, node_key="n_script",
                                       model_id="gpt-5.5")])
    await _result(env, [step("node.set_model", project_id=pid, node_key="n_script",
                             model_id="gpt-5.6-luna")])
    view = await _run(env, [step("history.rollback", snapshot_id=changed["snapshot_id"])],
                      approve=True)
    assert view["status"] != "succeeded"
    assert _model(await _meta(env, pid)) == "gpt-5.6-luna"


async def test_params_change_rolls_back_via_history(env) -> None:
    pid = await _project(env)
    res = await _result(env, [step("node.set_params", project_id=pid, node_key="n_review",
                                   disabled=True,
                                   branches=[{"edge_id": "e3", "kind": "pass"}])])
    snap = await store.get_snapshot(res["snapshot_id"])
    assert snap.kind == "node_params"
    assert snap.before == {"disabled": [], "branches": {"e3": "after"}}
    assert snap.after == {"disabled": ["n_review"], "branches": {"e3": "pass"}}
    meta = await _meta(env, pid)
    assert meta["disabled_nodes"] == ["n_review"]

    back = await _result(env, [step("history.rollback", snapshot_id=res["snapshot_id"])],
                         approve=True)
    assert "вернул прежние настройки" in back["text_ru"]
    meta = await _meta(env, pid)
    assert meta["disabled_nodes"] == []
    e3 = next(e for e in meta["canvas_graph"]["edges"] if e["id"] == "e3")
    assert e3["data"]["kind"] == "after"
    # Сам откат тоже обратим: обратный снимок возвращает правку.
    again = await _result(env, [step("history.rollback",
                                     snapshot_id=back["reverse_snapshot_id"])], approve=True)
    assert again["restored_snapshot_id"] == back["reverse_snapshot_id"]
    assert (await _meta(env, pid))["disabled_nodes"] == ["n_review"]


def _e3(meta: dict) -> str:
    return next(e for e in meta["canvas_graph"]["edges"] if e["id"] == "e3")["data"]["kind"]


async def test_params_rollback_out_of_order_touches_only_its_settings(env) -> None:
    pid = await _project(env)
    early = await _result(env, [step("node.set_params", project_id=pid, node_key="n_review",
                                     disabled=True)])
    await _result(env, [step("node.set_params", project_id=pid, node_key="n_review",
                             branches=[{"edge_id": "e3", "kind": "pass"}])])
    await _result(env, [step("history.rollback", snapshot_id=early["snapshot_id"])],
                  approve=True)
    meta = await _meta(env, pid)
    assert meta["disabled_nodes"] == [] and _e3(meta) == "pass"
    # Откат не по порядку лёг в историю ноды: «верни прежние настройки» отменяет именно его.
    revert = [step("node.revert_params", project_id=pid, node_key="n_review")]
    await _result(env, revert)
    meta = await _meta(env, pid)
    assert meta["disabled_nodes"] == ["n_review"] and _e3(meta) == "pass"
    await _result(env, revert)
    assert _e3(await _meta(env, pid)) == "after"
    await _result(env, revert)
    assert (await _meta(env, pid))["disabled_nodes"] == []


async def test_restore_endpoint_rejects_foreign_values(env) -> None:
    pid = await _project(env)
    path = f"/api/projects/{pid}/nodes/n_review/params/restore"
    r = await env.client.post(path, json={"values": {"branches": {"e1": "pass"}}})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "edge_not_found"
    r = await env.client.post(path, json={"values": {"retry_profile": "slow"}})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "bad_param"
    r = await env.client.post(path, json={"values": {"disabled": ["n_review"]},
                                          "expected": {"disabled": ["n_fix"]}})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "changed_since"
    assert "disabled_nodes" not in await _meta(env, pid)


async def test_project_settings_roll_back_via_history(env) -> None:
    pid = await _project(env)
    before = (await env.client.get(f"/api/projects/{pid}")).json()
    res = await _result(env, [step("project.patch_settings", project_id=pid,
                                   title="Новое название", aspect_ratio="9:16")])
    snap = await store.get_snapshot(res["snapshot_id"])
    assert snap.kind == "project_settings"
    assert snap.target["keys"] == ["aspect_ratio", "title"]
    assert snap.before == {"aspect_ratio": before["aspect_ratio"], "title": before["title"]}
    assert snap.after == {"aspect_ratio": "9_16", "title": "Новое название"}

    await _result(env, [step("history.rollback", snapshot_id=res["snapshot_id"])], approve=True)
    now = (await env.client.get(f"/api/projects/{pid}")).json()
    assert now["title"] == before["title"]
    assert now["aspect_ratio"] == before["aspect_ratio"]


async def test_snapshot_list_shows_model_and_settings(env) -> None:
    pid = await _project(env)
    await _result(env, [step("node.set_model", project_id=pid, node_key="n_script",
                             model_id="gpt-5.6-luna")])
    await _result(env, [step("project.patch_settings", project_id=pid, title="Другое")])
    listed = await _result(env, [step("history.list_snapshots", project_id=pid)])
    kinds = {row["kind"] for row in listed["snapshots"]}
    assert {"node_model", "project_settings"} <= kinds
