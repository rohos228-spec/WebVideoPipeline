"""«Сделай, чтобы во всех картинках был пластилин» — правка промта картинок, а не «не умею»."""

from __future__ import annotations

import asyncio

import pytest

from app.orchestrator.agents import style_scope


@pytest.mark.parametrize(
    "text, style",
    [
        ("сделай чтобы во всех картинках был пластилин", "пластилин"),
        ("поменяй стиль картинок на нуар в 4 проекте", "нуар"),
        ("хочу вязаный стиль в кадрах", "вязаный"),
        ("картинки без стиля нуар? проверь", None),
        ("почему в картинках нет пластилина", None),
        ("сделай картинки", None),
    ],
)
def test_wanted_style(text: str, style: str | None) -> None:
    assert style_scope.wanted_style(text) == style


def test_style_plan_edits_image_prompts(monkeypatch) -> None:
    async def node(_pid: int) -> str:
        return "n_image_prompts"

    monkeypatch.setattr(style_scope, "_image_prompts_node", node)
    plan = asyncio.run(style_scope.style_plan("сделай чтобы во всех картинках был пластилин", 4))
    assert len(plan) == 1 and plan[0].action == "prompt.edit_text"
    assert plan[0].params["node_key"] == "n_image_prompts" and plan[0].params["project_id"] == 4
    assert "пластилин" in plan[0].params["append"]


@pytest.mark.parametrize(
    "text, target",
    [
        ("сделай все картинки в стиле аниме", "images"),
        ("сделай картинки темнее", "images"),
        ("добавь в каждый кадр луну", "images"),
        ("убери из промтов кровь", "images"),
        ("текст озвучки слишком длинный, сократи", "script"),
        ("перепиши сценарий смешнее", "script"),
        ("сделай картинки", None),
        ("запусти картинки", None),
        ("в кадре 2 сделай сову белой", None),
        ("сделай картинки для первых двух кадров", None),
        ("почему картинки темные?", None),
        ("сделай музыку тише", None),
    ],
)
def test_wide_edit_target(text, target):
    assert style_scope.wide_edit_target(text) == target


def test_wide_edit_plan_appends_wish(monkeypatch):
    async def node(pid, typ):
        return "n_image_prompts" if typ == "image_prompts" else "n_script"

    monkeypatch.setattr(style_scope, "_node_of_type", node)
    plan = asyncio.run(style_scope.wide_edit_plan("сделай картинки темнее", 10))
    assert plan[0].action == "prompt.edit_text"
    assert plan[0].params["node_key"] == "n_image_prompts"
    assert "картинки темнее" in plan[0].params["append"]
