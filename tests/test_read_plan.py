"""Простые чтения в чате: план кодом и ответ текстом шага, без модели."""

from __future__ import annotations

import pytest

from app.orchestrator.agents import read_plan as rp


class _NoModel:
    def __getattr__(self, name):
        raise AssertionError("модель не должна вызываться")


@pytest.mark.parametrize(
    ("text", "action", "params"),
    [
        ("покажи все проекты", "projects.overview", {"limit": 200}),
        ("Покажи все проекты.", "projects.overview", {"limit": 200}),
        ("покажи мне все мои проекты", "projects.overview", {"limit": 200}),
        ("список проектов", "projects.overview", {"limit": 200}),
        ("список всех проектов, пожалуйста", "projects.overview", {"limit": 200}),
        ("выведи проекты", "projects.overview", {"limit": 200}),
        ("какие у меня есть проекты?", "projects.overview", {"limit": 200}),
        ("сколько у меня проектов", "projects.overview", {"limit": 200}),
        ("какие проекты сейчас идут", "projects.overview", {"status": "running"}),
        ("покажи запущенные проекты", "projects.overview", {"status": "running"}),
        ("статус проекта 7", "project.status", {"project_id": 7}),
        ("покажи статус проекта #12", "project.status", {"project_id": 12}),
        ("что с проектом 5?", "project.status", {"project_id": 5}),
        ("где сейчас проект 9", "project.status", {"project_id": 9}),
    ],
)
def test_simple_reads_are_planned_by_code(text, action, params) -> None:
    step = rp.read_step(text)
    assert step is not None
    assert (step["action"], step["params"]) == (action, params)
    assert step["mutating"] is False and step["paid"] is False and step["risk"] == "none"


@pytest.mark.parametrize(
    "text",
    [
        "покажи все проекты и запусти 7",
        "покажи все проекты кроме 5",
        "покажи проекты с ошибкой",
        "почему проект 7 встал",
        "что с проектом 7 и почему он на паузе",
        "статус проекта 7 и 8",
        "запусти все проекты",
        "удали проекты",
        "покажи все проекты, которые на паузе",
        "сделай отчет по всем проектам",
        "покажи траты по проектам",
        "",
    ],
)
def test_anything_else_is_left_to_the_model(text) -> None:
    assert rp.read_step(text) is None


async def test_router_planner_lists_projects_without_the_model() -> None:
    from app.orchestrator.agents.planner import RouterPlanner

    planner = RouterPlanner(_NoModel())
    steps = await planner.make_plan("покажи все проекты")
    assert [(s.action, s.params) for s in steps] == [("projects.overview", {"limit": 200})]
    assert planner.last_from_code is True and steps[0].risk == "none"


def _overview_note():
    from app.orchestrator.agents.summarizer import note_from_result

    body = {
        "filter": None,
        "total": 2,
        "shown": 2,
        "projects": [
            {"id": 7, "title": "Сова", "status_ru": "на паузе", "pause_reason": "ждёт проверки", "error": None},
            {"id": 4, "title": "Лиса", "status_ru": "готов", "pause_reason": None, "error": None},
        ],
        "text_ru": "Проектов: 2; показано 2, идут сейчас 0, требуют внимания 1. #7 «Сова»: на паузе. #4 «Лиса»: готов.",
    }
    return note_from_result("projects.overview", {"status": "ok", "result": body}, readonly=True)


async def test_summarizer_shows_project_list_without_model() -> None:
    from app.orchestrator.agents.summarizer import TaskFacts, TaskSummarizer

    facts = TaskFacts(goal="покажи все проекты", status="done", steps=[_overview_note()])
    text = await TaskSummarizer(_NoModel()).summarize(facts, run_id="r")
    assert text.splitlines() == [
        "Проектов: 2; показано 2, идут сейчас 0, требуют внимания 1.",
        "• #7 «Сова»: на паузе — ждёт проверки",
        "• #4 «Лиса»: готов",
    ]


async def test_other_goal_with_same_step_still_asks_the_model() -> None:
    from app.orchestrator.agents.summarizer import TaskFacts, TaskSummarizer

    facts = TaskFacts(goal="какие проекты требуют внимания и почему", status="done", steps=[_overview_note()])
    assert TaskSummarizer(object())._wants_model(facts) is True
