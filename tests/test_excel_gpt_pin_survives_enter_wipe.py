"""Pin active_excel_gpt_node_key переживает wipe при входе в enriching_N.

Живой лог _chat_test2 #14, 2026-10-21 13:38:54:
.203 pin excel_gpt node_key=n_excel_gpt_2 → .253 replace_step_outputs_on_enter
(wipe excel_gpt, clear_slot_completion_meta снимал ключ) → .493 pull_master_runtime
(ключа в master нет — project.db оставил свой n_excel_gpt_1) → .528 keep n_excel_gpt_1.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.models import ProjectStatus
from app.services.step_replace import replace_step_outputs_on_enter

_NODES = [
    {"id": "n_excel_gpt_1", "type": "excel_gpt", "position": {"x": 1}, "data": {"slotIndex": 1}},
    {"id": "n_excel_gpt_2", "type": "excel_gpt", "position": {"x": 2}, "data": {"slotIndex": 2}},
]


class _Session:
    async def flush(self):
        return None

    async def execute(self, *_a, **_k):  # граф берётся из meta.canvas_graph
        raise AssertionError("DB not expected")


def _project(active: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=14,
        status=ProjectStatus.items_ready,
        prompt_overrides={},
        meta={
            "active_excel_gpt_node_key": active,
            "excel_gpt_completed_keys": ["n_excel_gpt_1"],
            "enrich_completed_slots": [1],
            "canvas_graph": {"nodes": _NODES, "edges": []},
        },
    )


@pytest.mark.asyncio
async def test_enter_wipe_keeps_freshly_pinned_slot_key() -> None:
    p = _project("n_excel_gpt_2")
    await replace_step_outputs_on_enter(_Session(), p, ProjectStatus.enriching_2)
    assert p.meta.get("active_excel_gpt_node_key") == "n_excel_gpt_2"


@pytest.mark.asyncio
async def test_enter_wipe_does_not_resurrect_key_of_other_slot() -> None:
    p = _project("n_excel_gpt_1")
    await replace_step_outputs_on_enter(_Session(), p, ProjectStatus.enriching_2)
    # ключ чужого слота не возвращаем (его снимает wipe, слот резолвится заново)
    assert p.meta.get("active_excel_gpt_node_key") is None
