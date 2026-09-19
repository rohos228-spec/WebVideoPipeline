"""Overflow QC: BFS после группы не хватает чужой slot=1."""

from __future__ import annotations

from app.models import Project, ProjectStatus
from app.orchestrator.graph.planner import WorkflowGraph

# Группа «Сценарий → промпты кадров + QC» на канвасе: overflow.
_OVERFLOW_GROUP = (
    ("n_excel_gpt_fw_script", "GPT: сценарист"),
    ("n_excel_gpt_fw_check_script", "Проверка: сценарий"),
    ("n_excel_gpt_fw_action", "GPT: главное действие · по битам"),
    ("n_excel_gpt_fw_shots", "GPT: сцены → кадры"),
    ("n_excel_gpt_fw_frames", "GPT: промты кадров · continuity"),
    ("n_excel_gpt_fw_qc", "GPT: QC промптов"),
)


def _overflow_node(nid: str, label: str, x: float) -> dict:
    return {
        "id": nid,
        "type": "excel_gpt",
        "position": {"x": x, "y": 0},
        "data": {
            "label": label,
            "slotOverflow": True,
            "groupId": "scenariy_prompty_kadrov_qc",
            "groupTitle": "Сценарий → промпты кадров + QC",
        },
    }


def _overflow_nodes() -> list[dict]:
    return [_overflow_node(nid, label, i * 290) for i, (nid, label) in enumerate(_OVERFLOW_GROUP)]


def _overflow_edges() -> list[dict]:
    pairs = [
        ("n_excel_gpt_fw_script", "n_excel_gpt_fw_check_script"),
        ("n_excel_gpt_fw_check_script", "n_excel_gpt_fw_action"),
        ("n_excel_gpt_fw_action", "n_excel_gpt_fw_shots"),
        ("n_excel_gpt_fw_shots", "n_excel_gpt_fw_frames"),
        ("n_excel_gpt_fw_frames", "n_excel_gpt_fw_qc"),
    ]
    return [{"id": f"e_{a}_{b}", "source": a, "target": b} for a, b in pairs]


def test_overflow_qc_does_not_start_stray_slot1_then_hero() -> None:
    """После QC overflow-группы не хватать чужой excel_gpt slot=1 → hero."""
    from app.services.excel_gpt_node import prepare_enrich_chain_for_auto_advance

    stray = "n_excel_gpt_1787849035232"
    nodes = _overflow_nodes() + [
        {
            "id": stray,
            "type": "excel_gpt",
            "position": {"x": 0, "y": 200},
            "data": {"slotIndex": 1, "label": "Работа с GPT"},
        },
        {
            "id": "n_hero_1786493259413",
            "type": "hero",
            "position": {"x": 200, "y": 200},
            "data": {"label": "Персонажи"},
        },
        {
            "id": "n_image_prompts",
            "type": "image_prompts",
            "position": {"x": 400, "y": 200},
            "data": {"label": "Промты картинок"},
        },
    ]
    edges = _overflow_edges() + [
        {"id": "e_stray_hero", "source": stray, "target": "n_hero_1786493259413"},
        {
            "id": "e_hero_imgpr",
            "source": "n_hero_1786493259413",
            "target": "n_image_prompts",
        },
    ]
    g = WorkflowGraph(nodes, edges)
    p = Project(
        topic="t",
        slug="t",
        status=ProjectStatus.enrich_1_ready,
        meta={
            "canvas_graph": {"nodes": nodes, "edges": edges},
            "active_excel_gpt_node_key": "n_excel_gpt_fw_qc",
            "excel_gpt_completed_keys": [nid for nid, _ in _OVERFLOW_GROUP],
        },
    )
    assert prepare_enrich_chain_for_auto_advance(p, ProjectStatus.enrich_1_ready) is None
    found = g.next_work_node_after_ready(p, ProjectStatus.enrich_1_ready)
    assert found is None, f"after overflow QC started {found}"
    assert g.next_running_after_ready(p, ProjectStatus.enrich_1_ready) is None
