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


def test_gpt_step_without_node_takes_first_chain_head(monkeypatch: pytest.MonkeyPatch) -> None:
    meta = {
        "canvas_graph": {
            "nodes": [
                {"id": "n_split", "type": "split"},
                {"id": "n_excel_gpt_1", "type": "excel_gpt"},
                {"id": "n_excel_gpt_2", "type": "excel_gpt"},
            ],
            "edges": [],
        }
    }

    async def meta_of(_pid: int) -> dict:
        return meta

    done: set[str] = set()

    async def done_of(_pid: int) -> set[str]:
        return done

    monkeypatch.setattr(project_nodes, "_meta_from_db", meta_of)
    monkeypatch.setattr(executor, "_done_nodes", done_of)
    out = asyncio.run(executor._fill_gpt_head({"project_id": 26, "step_code": "excel_gpt"}))
    assert out["node_key"] == "n_excel_gpt_1"
    done.add("n_excel_gpt_1")
    nxt = asyncio.run(executor._fill_gpt_head({"project_id": 26, "step_code": "excel_gpt"}))
    assert nxt["node_key"] == "n_excel_gpt_2"
    same = asyncio.run(executor._fill_gpt_head({"project_id": 26, "step_code": "excel_gpt", "node_key": "n_excel_gpt_2"}))
    assert same["node_key"] == "n_excel_gpt_2"
    other = asyncio.run(executor._fill_gpt_head({"project_id": 26, "step_code": "images"}))
    assert "node_key" not in other


def test_planner_hint_names_next_gpt_node(monkeypatch: pytest.MonkeyPatch) -> None:
    async def done_of(_pid: int) -> set[str]:
        return {"n_excel_gpt_1"}

    monkeypatch.setattr(executor, "_done_nodes", done_of)
    line = asyncio.run(project_nodes._next_gpt_line(26, ROWS, "запусти работу с gpt в 26"))
    assert "n_excel_gpt_2" in line
    assert asyncio.run(project_nodes._next_gpt_line(26, ROWS, "запусти сценарий")) == ""
