"""Тесты функции active_key_belongs_to_other_slot для предотвращения сбоев в enrich_xlsx."""

from __future__ import annotations

from types import SimpleNamespace

from app.models import ProjectStatus
from app.services.excel_gpt_node import active_key_belongs_to_other_slot


def _make_project_with_slots():
    nodes = [
        {
            "id": "node_enrich_1",
            "type": "excel_gpt",
            "position": {"x": 100, "y": 0},
            "data": {"slotIndex": 1, "label": "Обогащение 1"},
        },
        {
            "id": "node_enrich_2",
            "type": "excel_gpt",
            "position": {"x": 300, "y": 0},
            "data": {"slotIndex": 2, "label": "Обогащение 2"},
        },
        {
            "id": "node_enrich_3",
            "type": "excel_gpt",
            "position": {"x": 500, "y": 0},
            "data": {"slotIndex": 3, "label": "Обогащение 3"},
        },
    ]
    edges = [
        {"id": "e1", "source": "node_enrich_1", "target": "node_enrich_2"},
        {"id": "e2", "source": "node_enrich_2", "target": "node_enrich_3"},
    ]
    return SimpleNamespace(
        id=42,
        status=ProjectStatus.enriching_1,
        meta={
            "canvas_graph": {"nodes": nodes, "edges": edges},
            "active_excel_gpt_node_key": "node_enrich_1",
        },
    )


def test_import_from_excel_gpt_node():
    """Проверяем, что импорт функции в enrich_xlsx не вызывает ImportError."""
    from app.services.excel_gpt_node import active_key_belongs_to_other_slot as fn

    assert callable(fn)


def test_active_key_empty_or_none():
    """Если активный ключ не задан, функция возвращает False (делегируя резолверу)."""
    project = _make_project_with_slots()
    assert active_key_belongs_to_other_slot(project, None, 1) is False
    assert active_key_belongs_to_other_slot(project, "", 1) is False
    assert active_key_belongs_to_other_slot(project, "   ", 1) is False


def test_slot_idx_out_of_bounds():
    """Слот вне диапазона 1..5 не считается валидным слотом."""
    project = _make_project_with_slots()
    assert active_key_belongs_to_other_slot(project, "node_enrich_1", 0) is False
    assert active_key_belongs_to_other_slot(project, "node_enrich_1", 6) is False


def test_active_key_matches_current_slot():
    """Если ключ принадлежит текущему слоту, возвращает False."""
    project = _make_project_with_slots()
    assert active_key_belongs_to_other_slot(project, "node_enrich_1", 1) is False
    assert active_key_belongs_to_other_slot(project, "node_enrich_2", 2) is False
    assert active_key_belongs_to_other_slot(project, "node_enrich_3", 3) is False


def test_active_key_belongs_to_other_slot():
    """Если ключ принадлежит другому слоту (например, хвост от шага 1 на шаге 2), возвращает True."""
    project = _make_project_with_slots()
    # На шаге 2 передан ключ от шага 1
    assert active_key_belongs_to_other_slot(project, "node_enrich_1", 2) is True
    # На шаге 1 передан ключ от шага 3
    assert active_key_belongs_to_other_slot(project, "node_enrich_3", 1) is True


def test_active_key_unknown_or_stale():
    """Если ключ указывает на несуществующую ноду, возвращает True для повторного резолва."""
    project = _make_project_with_slots()
    assert active_key_belongs_to_other_slot(project, "deleted_node_xyz", 1) is True
