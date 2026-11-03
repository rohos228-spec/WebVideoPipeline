from app.services import node_edit as ne

META = {
    "canvas_graph": {
        "nodes": [{"id": "n_images", "type": "images", "data": {"label": "Картинки"}}],
        "edges": [],
    }
}


def _state(gid):
    return {"id": 12, "meta": META, "image_generator": gid, "prompt_overrides": {}}


def test_unknown_project_generator_is_shown():
    out = ne.describe_node(_state("gpt_image_2_vip"), "n_images")
    assert out["model"]["from_project"] and out["model"]["known"] is False
    assert "gpt_image_2_vip" in out["text_ru"] and "нет в каталоге" in out["text_ru"]


def test_no_project_generator_keeps_catalog_default():
    out = ne.describe_node(_state(None), "n_images")
    assert not out["model"].get("from_project")


def test_roster_shows_project_generator():
    from app.orchestrator.agents.project_nodes import roster

    rows = roster(META, "gpt_image_2_vip")
    assert "gpt_image_2_vip из настроек проекта" in rows[0]["model"]
    assert "нет в каталоге" in rows[0]["model"]
