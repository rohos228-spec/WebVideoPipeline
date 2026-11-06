import asyncio
from types import SimpleNamespace

from app.orchestrator.agents.intent_rewrite import _clean, canonical_goal


class _Router:
    def __init__(self, text):
        self.text = text
        self.calls = []

    async def call(self, tier, messages, stream, **kw):
        self.calls.append((tier, messages, kw))
        return SimpleNamespace(text=self.text)


_KW = {"run_id": "orch-test-1", "task_id": 1, "budget_scope": "internal"}


import pytest


@pytest.fixture(autouse=True)
def _enabled(monkeypatch):
    import app.orchestrator.agents.intent_rewrite as ir

    monkeypatch.setattr(ir, "ENABLED", True)


def test_rewrite_maps_slang_to_command():
    router = _Router("кадр 2 переделай")
    out = asyncio.run(canonical_goal("кит на втором стрёмный, давай по новой", router, _KW))
    assert out == "кадр 2 переделай"
    assert router.calls[0][0] == "fast"


def test_rewrite_none_and_garbage_ignored():
    assert asyncio.run(canonical_goal("ну такое", _Router("NONE"), _KW)) is None
    assert _clean("в кадре <N> сделай <что>") is None
    assert _clean("«музыку потише»") == "музыку потише"
    assert asyncio.run(canonical_goal("музыку потише", _Router("музыку потише"), _KW)) is None


def test_rewrite_skips_questions_and_no_router():
    router = _Router("кадр 2 переделай")
    assert asyncio.run(canonical_goal("а почему кит кривой?", router, _KW)) is None
    assert router.calls == []
    assert asyncio.run(canonical_goal("кадр 2 переделай", None, _KW)) is None


def test_rewrite_timeout_returns_none(monkeypatch):
    import app.orchestrator.agents.intent_rewrite as ir

    class _Slow:
        async def call(self, *a, **k):
            await asyncio.sleep(1)

    monkeypatch.setattr(ir, "REWRITE_TIMEOUT_S", 0.01)
    assert asyncio.run(canonical_goal("перерисуй вторую", _Slow(), _KW)) is None
