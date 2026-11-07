from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("app.orchestrator.actions")

from app.orchestrator import queue as orch_queue
from app.orchestrator.agents.runtime import build_runtime, set_runtime
from tests.agents.mocks import FixedPlanner, MockActions, MockValidators, ScriptedRouter


class _SlowRouter(ScriptedRouter):
    async def classify(self, *args, **kwargs):
        self.classify_calls += 1
        await asyncio.sleep(30)
        raise AssertionError("must be cut by the deadline")


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["покажи ошибки за последний час", "сравни 5 и 7 проект"])
async def test_slow_classifier_falls_back_by_phrase(token: str, monkeypatch, text: str) -> None:
    del token
    assert orch_queue.classify_deterministic(text) is None
    question = orch_queue.is_status_question(text) or orch_queue.is_plain_question(text)
    mode = "append" if question else "task"
    monkeypatch.setattr(orch_queue, "CLASSIFY_DEADLINE_S", 0.2)
    router = _SlowRouter()
    rt = build_runtime(actions=MockActions(), validators=MockValidators(), router=router, planner=FixedPlanner([]))
    set_runtime(rt)
    session = "deadline-" + str(abs(hash(text)))
    try:
        await rt.queue.accept(session, text)
        await rt.queue.drain(timeout=5)
        kinds = [event for event in rt.bus.replay(session, 0) if event.type == "classification"]
        assert router.classify_calls == 1
        assert kinds and kinds[-1].payload["mode"] == mode
    finally:
        set_runtime(None)
