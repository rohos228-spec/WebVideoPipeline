"""«Отмени последнее» при живой задаче: остановка или откат — решает классификатор, не правило отката."""

from __future__ import annotations

import pytest

from app.orchestrator.agents.runtime import build_runtime, set_runtime
from app.orchestrator.contracts import MessageMode, PlanStep, TaskStatus
from app.orchestrator.queue import cancel_or_undo
from tests.agents.mocks import FixedPlanner, MockActions, MockValidators, ScriptedRouter


def test_only_cancel_word_undo_is_ambiguous() -> None:
    assert cancel_or_undo("отмени последнее")
    assert not cancel_or_undo("верни как было")
    assert not cancel_or_undo("стоп, отмени последнее")
    assert not cancel_or_undo("отмени последнюю задачу")


@pytest.mark.asyncio
async def test_cancel_last_during_running_task_asks_classifier(token: str) -> None:
    del token
    session = "undo-or-stop"
    actions = MockActions()
    router = ScriptedRouter()
    router.mode = MessageMode.interrupt
    rt = build_runtime(
        actions=actions,
        validators=MockValidators(),
        router=router,
        planner=FixedPlanner(
            [PlanStep(action="mock_sleep", params={"_block": True, "_seconds": 30}, expected="wait",
                      paid=False, mutating=False)]
        ),
    )
    set_runtime(rt)
    try:
        task_id = await rt.supervisor.start_task(session, "долгий шаг")
        await actions.started.wait()
        await rt.queue.accept(session, "отмени последнее")
        await rt.supervisor.wait(task_id, timeout=8)
        assert router.classify_calls == 1
        assert rt.tasks.get(task_id)["status"] == TaskStatus.cancelled.value
    finally:
        set_runtime(None)
