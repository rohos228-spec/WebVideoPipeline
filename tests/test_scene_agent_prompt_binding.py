"""Вариант промта агента веера и жанр берутся из конфига узла.

Часть находки 12: привязка жила в `meta.prompt_slot_variants`, теперь читается
через единый слой `node_config`. Оба пути должны давать один ответ, иначе
переезд конфига поменял бы поведение старых роликов.
"""

from __future__ import annotations

from app.services.scene_design import agents as ag


def _canvas(nodes: list[dict]) -> dict:
    return {"canvas_graph": {"workflow_id": 2, "nodes": nodes, "edges": []}}


class _P:
    def __init__(self, meta: dict) -> None:
        self.meta = meta


def test_variant_from_legacy_meta() -> None:
    p = _P({"prompt_slot_variants": {"n_a": {"main": "sd_action_chrono_dyn"}}})
    assert ag.project_scene_design_variant(p) == "chrono_dyn"


def test_variant_from_node_container() -> None:
    """Тот же ответ, когда привязка переехала в узел."""
    p = _P(
        _canvas(
            [
                {
                    "id": "n_a",
                    "type": "excel_gpt",
                    "data": {"config": {"promptSlots": {"main": "sd_action_chrono_dyn"}}},
                }
            ]
        )
    )
    assert ag.project_scene_design_variant(p) == "chrono_dyn"


def test_explicit_project_variant_wins() -> None:
    p = _P({"scene_design_variant": "chrono_dyn", "prompt_slot_variants": {}})
    assert ag.project_scene_design_variant(p) == "chrono_dyn"


def test_no_binding_means_no_variant() -> None:
    assert ag.project_scene_design_variant(_P({})) == ""


def test_node_prompt_variant_reads_container() -> None:
    p = _P(
        _canvas(
            [
                {
                    "id": "n_act",
                    "type": "excel_gpt",
                    "data": {
                        "sd_agent": "action",
                        "config": {"promptSlots": {"main": "sd_action_vlog"}},
                    },
                }
            ]
        )
    )
    assert ag._node_prompt_variant(p, "action") == "sd_action_vlog"


def test_node_prompt_variant_reads_legacy_bucket() -> None:
    meta = _canvas([{"id": "n_act", "type": "excel_gpt", "data": {"sd_agent": "action"}}])
    meta["prompt_slot_variants"] = {"n_act": {"main": "sd_action_chrono_dyn"}}
    assert ag._node_prompt_variant(_P(meta), "action") == "sd_action_chrono_dyn"


def test_agent_without_node_has_no_variant() -> None:
    p = _P(_canvas([{"id": "n_act", "type": "excel_gpt", "data": {"sd_agent": "camera"}}]))
    assert ag._node_prompt_variant(p, "action") is None
