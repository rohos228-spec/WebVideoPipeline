"""Модель пишет тип или подпись ноды вместо ключа: plan_resolve подставляет ключ n_…"""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("app.orchestrator.actions")

from app.orchestrator.actions import executor  # noqa: E402
from app.orchestrator.agents import project_nodes  # noqa: E402

ROWS = [
    {"key": "n_script", "type": "script", "step_code": "script", "label": "Закадровый текст", "type_ru": "Закадровый текст"},
    {"key": "n_excel_gpt_1", "type": "excel_gpt", "step_code": "excel_gpt", "label": "Работа с GPT", "type_ru": "Работа с GPT"},
    {"key": "n_excel_gpt_2", "type": "excel_gpt", "step_code": "excel_gpt", "label": "Работа с GPT", "type_ru": "Работа с GPT"},
]


@pytest.fixture(autouse=True)
def _nodes(monkeypatch: pytest.MonkeyPatch) -> None:
    async def meta(_pid: int) -> dict:
        return {}

    monkeypatch.setattr(project_nodes, "_meta_from_db", meta)
    monkeypatch.setattr(project_nodes, "roster", lambda _meta: ROWS)


def _fill(node_key: str) -> str:
    return asyncio.run(executor.plan_resolve("node.set_model", {"project_id": 12, "node_key": node_key}))["node_key"]


def test_type_and_label_become_the_key() -> None:
    assert _fill("script") == "n_script"
    assert _fill("Закадровый текст") == "n_script"


def test_real_key_and_ambiguous_type_stay() -> None:
    assert _fill("n_script") == "n_script"
    assert _fill("excel_gpt") == "excel_gpt"
