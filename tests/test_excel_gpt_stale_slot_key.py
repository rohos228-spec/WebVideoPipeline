"""enriching_2 с ключом n_excel_gpt_1 (слот 1, уже done) не должен «пропускать GPT».

Живые данные _chat_test2 (#10/#12/#14, 2026-10-21 13:38): после items_ready
статус enriching_2, а meta.active_excel_gpt_node_key = n_excel_gpt_1 (в
excel_gpt_completed_keys). auto_advance писал «keep … no restart», enrich_xlsx —
«already in completed_keys — skip GPT», статус enrich_2_ready без второго
обогащения; NodeRun n_excel_gpt_2 так и остался pending. img_pr потом получил
кадры без place/фона/действия и отвечал «Нет исходных данных для заполнения».
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.models import ProjectStatus
from app.services.excel_gpt_node import active_key_belongs_to_other_slot


def _project(status: ProjectStatus, active: str) -> SimpleNamespace:
    nodes = [
        {"id": "n_items", "type": "items", "position": {"x": 0}, "data": {}},
        {"id": "n_excel_gpt_1", "type": "excel_gpt", "position": {"x": 100}, "data": {"slotIndex": 1}},
        {"id": "n_excel_gpt_2", "type": "excel_gpt", "position": {"x": 200}, "data": {"slotIndex": 2}},
        {"id": "n_excel_gpt_fw", "type": "excel_gpt", "position": {"x": 300}, "data": {"slotOverflow": True}},
    ]
    return SimpleNamespace(
        id=14,
        status=status,
        meta={
            "active_excel_gpt_node_key": active,
            "excel_gpt_completed_keys": ["n_excel_gpt_1"],
            "enrich_completed_slots": [1],
            "canvas_graph": {"nodes": nodes, "edges": []},
        },
    )


def test_helper_flags_key_of_other_linear_slot_only() -> None:
    p = _project(ProjectStatus.enriching_2, "n_excel_gpt_1")
    assert active_key_belongs_to_other_slot(p, "n_excel_gpt_1", 2) is True
    assert active_key_belongs_to_other_slot(p, "n_excel_gpt_2", 2) is False
    # overflow (slot 0) живёт в enriching_1 — не трогаем
    assert active_key_belongs_to_other_slot(p, "n_excel_gpt_fw", 1) is False
    assert active_key_belongs_to_other_slot(p, "unknown", 2) is True
    assert active_key_belongs_to_other_slot(p, None, 2) is False


@pytest.mark.asyncio
async def test_prepare_enriching_2_with_slot1_key_starts_slot2_node(monkeypatch) -> None:
    from app.orchestrator import auto_advance

    p = _project(ProjectStatus.enriching_2, "n_excel_gpt_1")
    seen: list[str | None] = []

    async def _prepare(_session, _project, step_code, *, node_key=None, **_kw):
        seen.append(node_key)
        return True

    monkeypatch.setattr("app.services.run_sync.prepare_node_for_step_start", _prepare)
    monkeypatch.setattr(
        "app.services.project_meta.set_meta_fields",
        lambda project, **kw: project.meta.update(kw),
    )

    class _Session:
        async def refresh(self, _obj):
            return None

        async def flush(self):
            return None

    ok = await auto_advance._prepare_node_run_for_status(_Session(), p, ProjectStatus.enriching_2)
    assert ok is True
    assert seen == ["n_excel_gpt_2"]
    assert p.meta["active_excel_gpt_node_key"] == "n_excel_gpt_2"
