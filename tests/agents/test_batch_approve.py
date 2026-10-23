"""Одна карточка на однотипные шаги разных проектов.

Запуски одной parallel_group и подряд идущие настройки одного действия по
разным проектам спрашиваются одним нажатием. Grant одобряет ровно шаги из
карточки и только пока их отпечаток совпадает с планом. Тот же проект дважды
и долгий платный шаг без группы спрашиваются по одному.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.orchestrator.agents import approval_steps
from app.orchestrator.agents.runtime import set_runtime
from app.orchestrator.agents.supervisor import group_fingerprint
from app.orchestrator.contracts import PlanStep, TaskStatus
from tests.agents.mocks import MockValidators
from tests.agents.test_parallel_runs import _RUN, _approve_all, _Registry, _run, _runtime, _status

_SET = "node.set_model"


def _registry() -> _Registry:
    actions = _Registry(expect=3)
    actions.wait_for_all = False
    actions.table[_SET] = {"risk": "medium", "read_only": False, "destructive": False, "paid": False}
    return actions


def _set(project: int, model: str = "gpt-5-mini") -> PlanStep:
    return PlanStep(
        action=_SET,
        params={"project_id": project, "node_key": "gpt_1", "model_id": model},
        mutating=True,
        risk="medium",
    )


def _cards(runtime: Any, session: str) -> list[dict]:
    return [dict(event.payload) for event in runtime.bus.replay(session, 0) if event.type == "approval_required"]


def _calls(actions: _Registry, name: str) -> list[dict]:
    return [call for call in actions.calls if call["name"] == name]


@pytest.mark.asyncio
async def test_same_setting_in_three_projects_is_one_click(token: str) -> None:
    del token
    actions = _registry()
    validators = MockValidators()
    runtime = _runtime(actions, validators, [_set(1), _set(2), _set(3)])
    set_runtime(runtime)
    session = "batch-settings"
    try:
        task_id = await runtime.supervisor.start_task(session, "поставь gpt-5-mini в проектах 1, 2 и 3")
        cards = await _approve_all(runtime, session, task_id)
        assert len(cards) == 1
        card = cards[0]
        assert card["group"]["projects"] == [1, 2, 3]
        assert len(card["steps"]) == 3
        assert all("проект #" in step["params_text"] for step in card["steps"])
        assert runtime.tasks.get(task_id)["status"] == TaskStatus.done.value
        sets = _calls(actions, _SET)
        assert [call["params"]["project_id"] for call in sets] == [1, 2, 3]
        assert [call["ctx"]["approval"]["change_id"] for call in sets] == [
            f"{card['change_id']}-p{position}" for position in (0, 1, 2)
        ]
        # Проверки до и после идут у каждого шага, как без общей карточки.
        for project in (1, 2, 3):
            for phase in ("before", "after"):
                assert any(
                    call["context"].get("action") == _SET
                    and call["context"].get("phase") == phase
                    and call["project_id"] == project
                    and not call["context"].get("dry_run")
                    for call in validators.calls
                ), (project, phase)
    finally:
        set_runtime(None)


@pytest.mark.asyncio
async def test_same_project_twice_keeps_separate_cards(token: str) -> None:
    del token
    actions = _registry()
    runtime = _runtime(actions, MockValidators(), [_set(1), _set(1, "gpt-5")])
    set_runtime(runtime)
    session = "batch-same-project"
    try:
        task_id = await runtime.supervisor.start_task(session, "две смены модели в одном проекте")
        cards = await _approve_all(runtime, session, task_id)
        assert len(cards) == 2
        assert all("group" not in card for card in cards)
        assert runtime.tasks.get(task_id)["status"] == TaskStatus.done.value
    finally:
        set_runtime(None)


@pytest.mark.asyncio
async def test_long_paid_run_without_group_is_asked_one_by_one(token: str) -> None:
    """Подтверждение ядра живёт ограниченное время: долгие запуски по очереди заранее не одобряются."""
    del token
    actions = _registry()
    plan = [_status(7), _status(8), _run(7, group=None), _run(8, group=None)]
    runtime = _runtime(actions, MockValidators(), plan)
    set_runtime(runtime)
    session = "batch-long"
    try:
        task_id = await runtime.supervisor.start_task(session, "два проекта без группы")
        cards = await _approve_all(runtime, session, task_id)
        assert [card["action"] for card in cards] == [_RUN, _RUN]
        assert all("group" not in card for card in cards)
    finally:
        set_runtime(None)


@pytest.mark.asyncio
async def test_changed_plan_is_not_approved_by_old_card(token: str) -> None:
    """Отпечаток не совпал: grant ничего не одобряет, шаги спрашиваются новой карточкой."""
    del token
    actions = _registry()
    runtime = _runtime(actions, MockValidators(), [_set(1), _set(2)])
    set_runtime(runtime)
    session = "batch-changed"
    try:
        task_id = await runtime.supervisor.start_task(session, "две настройки")
        await runtime.supervisor.wait(task_id, timeout=5)
        first = _cards(runtime, session)[-1]
        positions, _mark = runtime.supervisor._group_cards[first["change_id"]]
        runtime.supervisor._group_cards[first["change_id"]] = (positions, "другой план")
        await runtime.supervisor.grant_approval(session, change_id=first["change_id"])
        await runtime.supervisor.wait(task_id, timeout=5)
        assert _calls(actions, _SET) == []
        assert runtime.tasks.get(task_id)["status"] == TaskStatus.waiting_approval.value
        second = _cards(runtime, session)[-1]
        assert second["change_id"] != first["change_id"]
        assert second["group"]["projects"] == [1, 2]
        await runtime.supervisor.grant_approval(session, change_id=second["change_id"])
        await runtime.supervisor.wait(task_id, timeout=5)
        assert [call["params"]["project_id"] for call in _calls(actions, _SET)] == [1, 2]
        assert runtime.tasks.get(task_id)["status"] == TaskStatus.done.value
    finally:
        set_runtime(None)


@pytest.mark.asyncio
async def test_group_card_survives_lost_memory(token: str) -> None:
    """После рестарта группа карточки читается из журнала событий."""
    del token
    actions = _registry()
    runtime = _runtime(actions, MockValidators(), [_set(1), _set(2), _set(3)])
    set_runtime(runtime)
    session = "batch-restart"
    try:
        task_id = await runtime.supervisor.start_task(session, "три настройки")
        await runtime.supervisor.wait(task_id, timeout=5)
        card = _cards(runtime, session)[-1]
        runtime.supervisor._group_cards.clear()
        runtime.supervisor._appr_loaded.discard(task_id)
        await runtime.supervisor.grant_approval(session, change_id=card["change_id"])
        await runtime.supervisor.wait(task_id, timeout=5)
        assert len(_cards(runtime, session)) == 1
        assert [call["params"]["project_id"] for call in _calls(actions, _SET)] == [1, 2, 3]
        assert runtime.tasks.get(task_id)["status"] == TaskStatus.done.value
    finally:
        set_runtime(None)


@pytest.mark.asyncio
async def test_rejected_group_card_runs_nothing(token: str) -> None:
    del token
    actions = _registry()
    runtime = _runtime(actions, MockValidators(), [_set(1), _set(2), _set(3)])
    set_runtime(runtime)
    session = "batch-reject"
    try:
        task_id = await runtime.supervisor.start_task(session, "три настройки")
        await runtime.supervisor.wait(task_id, timeout=5)
        card = _cards(runtime, session)[-1]
        await runtime.supervisor.reject_approval(session, change_id=card["change_id"])
        await runtime.supervisor.wait(task_id, timeout=5)
        assert _calls(actions, _SET) == []
        status = runtime.tasks.get(task_id)["status"]
        assert status in {TaskStatus.cancelled.value, TaskStatus.failed.value}
    finally:
        set_runtime(None)


def test_fingerprint_covers_every_step_of_the_group() -> None:
    plan = [_set(1), _set(2), _set(3)]
    mark = group_fingerprint(plan, [0, 1, 2])
    assert group_fingerprint(plan, [0, 1]) != mark
    changed = [_set(1), _set(2, "gpt-5"), _set(3)]
    assert group_fingerprint(changed, [0, 1, 2]) != mark
    assert group_fingerprint(list(plan), [0, 1, 2]) == mark


def test_group_card_sums_cost_and_tags_warnings_with_project() -> None:
    steps = [_run(1), _run(2)]
    views = [{"title": "Запуск", "paid": True, "risk": "high"} for _ in steps]
    previews: list[dict[str, Any]] = [
        {"cost": {"known": True}, "cost_estimate": 0.4, "warnings": ["мало кадров"], "plan_id": "p1"},
        {"cost": {"known": True}, "cost_estimate": 0.6, "destructive_effects": ["перезапишет картинки"]},
    ]
    card = approval_steps.group_card(steps, [3, 4], views, previews, "mark")
    assert card["cost_estimate"] == pytest.approx(1.0)
    assert card["cost_text"] == "около $1.0 за 2 проекта"
    assert card["warnings"] == ["проект #1: мало кадров"]
    assert card["destructive_effects"] == ["проект #2: перезапишет картинки"]
    assert card["group"] == {"positions": [3, 4], "projects": [1, 2], "fingerprint": "mark", "plan_ids": {"3": "p1"}}
    previews[1] = {"cost": {"known": False}}
    unknown = approval_steps.group_card(steps, [3, 4], views, previews, "mark")
    assert "cost_estimate" not in unknown
    assert unknown["cost_text"] == approval_steps.UNKNOWN_COST_TEXT


@pytest.mark.parametrize(
    ("count", "text"),
    [(1, "1 проект"), (2, "2 проекта"), (5, "5 проектов"), (11, "11 проектов"), (21, "21 проект"), (24, "24 проекта")],
)
def test_projects_ru(count: int, text: str) -> None:
    assert approval_steps.projects_ru(count) == text
