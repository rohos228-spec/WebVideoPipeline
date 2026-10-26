from __future__ import annotations

pytest_plugins = ['tests.action_layer_support']

import copy
from types import SimpleNamespace

import pytest

ne = pytest.importorskip("app.services.node_edit", reason="node_edit is not yet ported")

from app.models import Project, ProjectStatus
from app.services import vibecode_catalog as vc
from app.services.action_layer import executor
from app.services.action_layer import project_gen_settings as pgs
from app.services.action_layer.spec import ensure_builtin_actions, registry

AL = "/api/action-layer"
NODES = [
    {"id": "n_plan", "type": "plan", "data": {"label": "Сценарий"}},
    {"id": "n_hero", "type": "hero", "data": {"label": "Персонажи"}},
    {"id": "n_items", "type": "items", "data": {"label": "Предметы"}},
    {"id": "n_images", "type": "images", "data": {"label": "Картинки"}},
    {"id": "n_videos", "type": "videos", "data": {"label": "Видео"}},
]
CURRENT = {"image_generator": "gpt_image_2_vip", "image_resolution": "2k",
           "video_generator": "veo_3_1_lite", "video_resolution": "720p"}

def _meta(**data_by_node) -> dict:
    nodes = copy.deepcopy(NODES)
    for node in nodes:
        node["data"].update(data_by_node.get(node["id"], {}))
    return {"canvas_graph": {"workflow_id": 1, "nodes": nodes, "edges": []}}

@pytest.fixture(autouse=True)
def _builtin():
    ensure_builtin_actions()
    executor._CLAIMS.clear()
    yield
    executor._CLAIMS.clear()

@pytest.fixture
def not_managed(monkeypatch):
    async def _no(pid):
        return False
    monkeypatch.setattr("app.services.actions_v1.managed_project_enabled", _no)

def step(action, **params):
    return {"action_id": action, "params": params}

async def _project(env, meta: dict, **fields) -> int:
    async with env.factory() as s:
        p = Project(topic="t", slug=f"mm-{env.tmp_path.name[-6:]}-{len(fields)}",
                    status=ProjectStatus.new, meta=meta, prompt_overrides={}, **fields)
        s.add(p)
        await s.commit()
        return p.id

async def _row(env, pid) -> Project:
    async with env.factory() as s:
        return await s.get(Project, pid)

async def _run(env, steps, *, approve: bool = False) -> dict:
    r = await env.client.post(f"{AL}/plans/preview", json={"steps": steps})
    assert r.status_code == 200, r.text
    plan_id = r.json()["plan_id"]
    if approve:
        a = await env.client.post(f"{AL}/plans/{plan_id}/approve")
        assert a.status_code == 200, a.text
    run = await env.client.post(f"{AL}/plans/{plan_id}/run")
    assert run.status_code == 200, run.text
    await executor.wait(plan_id)
    return (await env.client.get(f"{AL}/plans/{plan_id}")).json()

@pytest.mark.xfail(strict=True, reason='merge line: image/video generator catalog and resolutions of main differ from int/chat-v1 (gpt_image_2_vip, nano_banana_2_lite, kling-3-0 unknown)')
def test_media_change_costs_use_the_node_model_catalog_prices() -> None:
    def cost(params):
        return [c["cost"] for c in pgs.media_changes(CURRENT, params)]

    assert cost({"image_generator": "nano_banana_2_lite"}) == ["cheaper"]
    assert cost({"image_generator": "flux_2_pro"}) == ["costlier"]
    assert cost({"image_resolution": "4k"}) == ["costlier"]
    assert cost({"image_resolution": "1k"}) == ["cheaper"]
    assert cost({"video_generator": "wan_2_7_t2v"}) == ["cheaper"]
    assert cost({"video_generator": "kling_2_6"}) == ["unknown"]
    assert cost({"image_generator": "nano_banana_2", "image_resolution": "4k"}) == ["costlier"]
    assert cost({"image_generator": "gpt_image_2_vip"}) == []
    # Veo 3.1 Lite идёт только в 720p: заявка 1080p цену не меняет.
    assert cost({"video_resolution": "1080p"}) == []
    # Пустое поле проекта = дефолт Studio.
    assert pgs.media_changes({}, {"image_generator": "z_image"})[0]["before_generator"] == (
        pgs.default_settings()["image_generator"])
    text = pgs.media_change_text(pgs.media_changes(CURRENT, {"image_generator": "flux_2_pro"})[0])
    assert "дороже" in text and "GPT Image 2" in text and "$0.15 за картинку" in text

@pytest.mark.xfail(strict=True, reason='merge line: image/video generator catalog and resolutions of main differ from int/chat-v1 (gpt_image_2_vip, nano_banana_2_lite, kling-3-0 unknown)')
def test_node_overrides_name_the_node_that_wins() -> None:
    project = {"image_generator": "z_image", "video_generator": "wan_2_7_t2v"}
    plain = pgs.node_overrides(_meta(), project)
    # Без modelId шаги картинок берут модель ноды по умолчанию, видео - модель проекта.
    assert len(plain) == 3 and all("по умолчанию" in n for n in plain)
    assert not any("«Видео»" in n for n in plain)
    explicit = pgs.node_overrides(_meta(n_images={"modelId": "flux-2-pro"},
                                        n_videos={"modelId": "kling-3-0"}), project)
    assert any("«Картинки»" in n and "выбрана «Flux 2 Pro»" in n for n in explicit)
    assert any("«Видео»" in n and "Kling" in n for n in explicit)
    same = pgs.node_overrides(_meta(n_images={"modelId": "z-image"}, n_hero={"modelId": "z-image"},
                                    n_items={"modelId": "z-image"}),
                              {"image_generator": "z_image"})
    assert same == []

@pytest.mark.parametrize(("node_key", "node_type", "model_id", "generator"), [
    ("n_images", "images", "nano-banana-2-lite", "nano_banana_2_lite"),
    ("n_hero", "hero", "flux-2-pro", "flux_2_pro"),
    ("n_items", "items", "z-image", "z_image"),
])
def test_set_model_on_image_nodes_reaches_the_step_generator(node_key, node_type, model_id,
                                                             generator) -> None:
    meta, _plan = ne.apply_model_change(_meta(), node_key, model_id)
    project = SimpleNamespace(meta=meta, image_generator="gpt_image_2_vip", image_resolution="2k",
                              image_quality=None, aspect_ratio="9_16")
    assert vc.effective_image_generator_id(project, node_type=node_type) == generator
    assert vc.resolve_node_media_settings(project, node_type=node_type)["image_generator_id"] == (
        generator)
    others = {"images", "hero", "items"} - {node_type}
    for other in others:
        assert vc.effective_image_generator_id(project, node_type=other) == "gpt_image_2_vip"

@pytest.mark.xfail(strict=True, reason='merge line: image/video generator catalog and resolutions of main differ from int/chat-v1 (gpt_image_2_vip, nano_banana_2_lite, kling-3-0 unknown)')
def test_set_model_on_videos_node_reaches_the_video_step_and_revert_returns_the_project_model() -> None:
    meta, _plan = ne.apply_model_change(_meta(), "n_videos", "kling-3-0")
    project = SimpleNamespace(meta=meta, video_generator="veo_3_1_lite")
    assert vc.effective_video_generator_id(project, node_type="videos") == "kling_3_0"
    back, _plan = ne.apply_model_change(meta, "n_videos", None, revert_of="x")
    project.meta = back
    assert vc.effective_video_generator_id(project, node_type="videos") == "veo_3_1_lite"

def test_catalog_entries_for_the_costly_variant() -> None:
    from app.orchestrator.action_contracts.contracts import CHAT_V1_ACTION_IDS
    from app.orchestrator.actions import metadata_of
    from app.services.action_layer import planner_rules
    from app.services.action_layer.planner_catalog import SYNONYMS

    cheap, costly = registry.get("project.patch_settings"), registry.get(
        "project.patch_settings_costly")
    assert cheap.risk == "local_safe" and not cheap.needs_approval
    assert costly.risk == "destructive" and costly.needs_approval and not costly.spends_money
    assert costly.params_schema == cheap.params_schema
    assert "project.patch_settings_costly" in CHAT_V1_ACTION_IDS
    assert metadata_of("project.patch_settings_costly").risk == "high"
    assert "project.patch_settings_costly" in SYNONYMS
    assert planner_rules.is_guarded(costly)

@pytest.mark.xfail(strict=True, reason='merge line: image/video generator catalog and resolutions of main differ from int/chat-v1 (gpt_image_2_vip, nano_banana_2_lite, kling-3-0 unknown)')
async def test_cheaper_model_is_saved_with_a_snapshot_and_rolls_back(env, not_managed) -> None:
    pid = await _project(env, _meta(n_images={"modelId": "flux-2-pro"}), **CURRENT)
    done = await _run(env, [step("project.patch_settings", project_id=pid,
                                 image_generator="nano_banana_2_lite")])
    assert done["status"] == "succeeded", done
    assert done["needs_approval"] is False
    res = done["steps"][0]["result"]
    assert res["media"][0]["cost"] == "cheaper" and "дешевле" in res["text_ru"]
    assert res["node_overrides"] and "«Картинки»" in res["text_ru"]
    assert res["rollback_possible"] is True and res["snapshot_id"]
    assert (await _row(env, pid)).image_generator == "nano_banana_2_lite"
    back = await _run(env, [step("history.rollback", snapshot_id=res["snapshot_id"])], approve=True)
    assert back["status"] == "succeeded", back
    assert "модель картинок «GPT Image 2», разрешение картинок 2K" in back["steps"][0]["result"]["text_ru"]
    row = await _row(env, pid)
    assert row.image_generator == "gpt_image_2_vip" and row.image_resolution == "2k"

@pytest.mark.xfail(strict=True, reason='merge line: image/video generator catalog and resolutions of main differ from int/chat-v1 (gpt_image_2_vip, nano_banana_2_lite, kling-3-0 unknown)')
async def test_costlier_or_unknown_model_needs_a_human(env, not_managed) -> None:
    pid = await _project(env, _meta(), **CURRENT)
    for params in ({"image_generator": "flux_2_pro"}, {"video_generator": "kling_2_6"},
                   {"image_resolution": "4k"}):
        r = await env.client.post(f"{AL}/plans/preview", json={"steps": [
            step("project.patch_settings", project_id=pid, **params)]})
        assert r.status_code == 422 and "media_not_costlier" in r.text, params
    r = await env.client.post(f"{AL}/plans/preview", json={"steps": [
        step("project.patch_settings_costly", project_id=pid, image_generator="flux_2_pro")]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["preview"]["needs_approval"] is True
    assert "дороже" in body["preview"]["destructive_effects"][0]["text"]
    assert (await env.client.post(f"{AL}/plans/{body['plan_id']}/run")).status_code == 403
    assert (await _row(env, pid)).image_generator == "gpt_image_2_vip"
    done = await _run(env, [step("project.patch_settings_costly", project_id=pid,
                                 image_generator="flux_2_pro")], approve=True)
    assert done["status"] == "succeeded", done
    assert (await _row(env, pid)).image_generator == "flux_2_pro"
    assert done["steps"][0]["result"]["snapshot_id"]

async def test_unknown_generator_is_refused_before_the_plan(env, not_managed) -> None:
    pid = await _project(env, _meta(), **CURRENT)
    for action in ("project.patch_settings", "project.patch_settings_costly"):
        r = await env.client.post(f"{AL}/plans/preview", json={"steps": [
            step(action, project_id=pid, image_generator="no_such_model")]})
        assert r.status_code == 422 and "генератор картинок" in r.text, action

async def test_title_only_change_has_no_media_text(env, not_managed) -> None:
    pid = await _project(env, _meta(), **CURRENT)
    done = await _run(env, [step("project.patch_settings", project_id=pid, title="Новое")])
    assert done["status"] == "succeeded", done
    res = done["steps"][0]["result"]
    assert "media" not in res and "node_overrides" not in res
    assert (await _row(env, pid)).title == "Новое"
