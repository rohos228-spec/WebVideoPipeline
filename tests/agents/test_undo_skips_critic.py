"""Откат «верни как было» собран кодом из журнала: критик его не судит, человек подтверждает карточкой."""

from __future__ import annotations

import pytest

from app.orchestrator.agents.planner import RouterPlanner
from app.orchestrator.agents.runtime import build_runtime, set_runtime
from app.services.action_layer import chat_undo
from tests.agents.mocks import MockActions, MockValidators, ScriptedRouter


class _NoRouter(ScriptedRouter):
    async def call(self, *args, **kwargs):
        raise AssertionError("модель не нужна")


class _CountingCritic:
    """Живой критик трижды требовал «подтверждение человека» и выдавал карточку «критик не одобрил»."""

    def __init__(self) -> None:
        self.calls = 0

    async def review(self, plan, context="", **kwargs) -> str:
        del plan, context, kwargs
        self.calls += 1
        return "revise"


@pytest.mark.asyncio
async def test_code_built_undo_plan_goes_to_risk_card_without_critic(token: str) -> None:
    del token

    async def undo(text, task_id):
        return chat_undo.UndoPlan([{"action": "history.rollback", "params": {"snapshot_id": 4, "project_id": 6},
                                    "mutating": True, "risk": "high"}])

    critic = _CountingCritic()
    rt = build_runtime(
        actions=MockActions(risks={"history.rollback": "high"}),
        validators=MockValidators(),
        router=_NoRouter(),
        planner=RouterPlanner(_NoRouter(), undo=undo),
        critic=critic,
    )
    set_runtime(rt)
    try:
        task_id = await rt.supervisor.start_task("undo-critic", "верни как было", project_id=6)
        await rt.supervisor.wait(task_id, timeout=5)
        cards = [e for e in rt.bus.replay("undo-critic", 0) if e.type == "approval_required"]
        assert critic.calls == 0
        assert cards and cards[-1].payload["reason"] not in {"critic_escalation", "critic_reject"}
    finally:
        set_runtime(None)



@pytest.mark.asyncio
@pytest.mark.parametrize("reason", [
    "Через этот чат ещё ничего не меняли или всё уже вернули — возвращать нечего",
    "Последняя правка чата поменяла сразу несколько мест: прежнюю модель «GPT 5.5» на ноде «Сценарий» "
    "в проекте #5; прежнюю модель «GPT 5.5» на ноде «Сценарий» в проекте #6. Напишите, что вернуть, "
    "например «верни как было в проекте 5»",
])
async def test_undo_answer_from_code_is_not_not_understood(token: str, reason: str) -> None:
    """«Возвращать нечего» и «что вернуть?» — ответ на понятую просьбу, а не «Не понял, что сделать»."""
    del token

    async def undo(text, task_id):
        return chat_undo.UndoPlan([], reason)

    rt = build_runtime(
        actions=MockActions(risks={"history.rollback": "high"}),
        validators=MockValidators(),
        router=_NoRouter(),
        planner=RouterPlanner(_NoRouter(), undo=undo),
        critic=_CountingCritic(),
    )
    set_runtime(rt)
    try:
        task_id = await rt.supervisor.start_task("undo-answer", "верни как было", project_id=6)
        await rt.supervisor.wait(task_id, timeout=5)
        texts = [e.payload.get("text") for e in rt.bus.replay("undo-answer", 0) if e.type == "message"]
        assert texts and texts[-1] == reason.rstrip(".") + "."
        assert "Не понял" not in texts[-1] and "Напишите номер проекта" not in texts[-1]
        assert not [e for e in rt.bus.replay("undo-answer", 0) if e.type == "approval_required"]
    finally:
        set_runtime(None)
