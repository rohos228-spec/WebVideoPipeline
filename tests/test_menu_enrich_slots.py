"""Слоты доработки считаются по графу, а не по колонке проекта.

Живой прогон 2026-08-31: в графе осталось два узла «Работы с GPT», в колонке
`enrich_slots_count` стояло три — линейный auto_advance искал несуществующий
третий слот. Правда должна быть одна, и это граф.
"""

from __future__ import annotations

import pytest

from app.telegram.menu import enabled_enrich_slots


def _canvas(nodes: list[dict]) -> dict:
    return {"canvas_graph": {"workflow_id": 2, "nodes": nodes, "edges": []}}


class _P:
    def __init__(self, meta: dict, column: int | None = 3) -> None:
        self.meta = meta
        self.enrich_slots_count = column


def test_no_project_gives_default() -> None:
    assert enabled_enrich_slots(None) == 3


def test_column_used_when_no_canvas() -> None:
    assert enabled_enrich_slots(_P({}, column=4)) == 4


def test_graph_wins_over_column() -> None:
    p = _P(
        _canvas(
            [
                {"id": "n1", "type": "excel_gpt", "data": {"slotIndex": 1}},
                {"id": "n2", "type": "excel_gpt", "data": {"slotIndex": 2}},
            ]
        ),
        column=3,
    )
    assert enabled_enrich_slots(p) == 2


def test_scene_agents_and_overflow_do_not_take_slots() -> None:
    """Агенты веера и проверки «вне слотов» в нумерации не участвуют."""
    p = _P(
        _canvas(
            [
                {"id": "n1", "type": "excel_gpt", "data": {"slotIndex": 1}},
                {"id": "sd", "type": "excel_gpt", "data": {"sd_agent": "action"}},
                {"id": "chk", "type": "excel_gpt", "data": {"slotOverflow": True}},
                {"id": "plan", "type": "plan", "data": {}},
            ]
        ),
        column=5,
    )
    assert enabled_enrich_slots(p) == 1


def test_canvas_without_slot_nodes_falls_back_to_column() -> None:
    """Граф есть, слотов в нём нет — считать нечего, работает колонка."""
    p = _P(_canvas([{"id": "plan", "type": "plan", "data": {}}]), column=2)
    assert enabled_enrich_slots(p) == 2


def test_result_is_clamped_to_allowed_range() -> None:
    nodes = [{"id": f"n{i}", "type": "excel_gpt", "data": {"slotIndex": i}} for i in range(1, 9)]
    assert enabled_enrich_slots(_P(_canvas(nodes), column=3)) == 5


def test_broken_graph_does_not_break_the_menu(monkeypatch: pytest.MonkeyPatch) -> None:
    """Счёт слотов упал — меню всё равно строится по колонке."""

    def boom(_node):
        raise RuntimeError("узел кривой")

    monkeypatch.setattr("app.services.excel_gpt_node.slot_index_from_node", boom)
    p = _P(_canvas([{"id": "n1", "type": "excel_gpt", "data": {"slotIndex": 1}}]), column=3)
    assert enabled_enrich_slots(p) == 3


def test_garbage_entry_in_nodes_is_skipped() -> None:
    """Не-dict в списке узлов пропускается, а не роняет подсчёт."""
    p = _P(
        _canvas(["мусор", {"id": "n1", "type": "excel_gpt", "data": {"slotIndex": 1}}]),
        column=3,
    )
    assert enabled_enrich_slots(p) == 1
