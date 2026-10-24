"""Ответ без задачи (append) не бывает пустым: пустой ответ модели — готовый ответ или честная заметка."""

from __future__ import annotations

import pytest
from sqlalchemy import text

from app.orchestrator.agents.question_context import parallel_limit_answer
from app.orchestrator.contracts import ApprovalEvent, ApprovalReason, LLMResponse, LLMUsage, TaskStatus
from app.orchestrator.db.migrations import connect
from app.orchestrator.queue import EMPTY_ANSWER_TEXT, EMPTY_BUDGET_TEXT
from app.orchestrator.routing.types import RouterExhausted

PARALLEL_QUESTION = "сколько проектов можно крутить одновременно"


def _assistant_texts(session_id: str) -> list[str]:
    conn = connect()
    try:
        rows = conn.execute(
            text(
                """
                SELECT content FROM orch_messages
                WHERE session_id = :sid AND role = 'assistant'
                ORDER BY id
                """
            ),
            {"sid": session_id},
        ).mappings().fetchall()
    finally:
        conn.close()
    return [str(row["content"]) for row in rows]


def _shown(runtime, session_id: str) -> list[str]:
    return [
        str(event.payload.get("text"))
        for event in runtime.bus.replay(session_id, 0)
        if event.type == "message" and event.payload.get("role") == "assistant"
    ]


def _empty_response(**extra) -> LLMResponse:
    return LLMResponse(
        text="",
        call_id="empty",
        model="gpt-5.6-sol",
        provider="vibecode",
        tier="strong",
        finish_reason="",
        usage=LLMUsage(),
        cost_usd=0.0,
        latency_ms=117000,
        fallback_index=2,
        deterministic=True,
        **extra,
    )


@pytest.mark.parametrize(
    ("count", "phrase"),
    [(1, "до 1 проекта"), (4, "до 4 проектов"), (10, "до 10 проектов"), (11, "до 11 проектов"), (21, "до 21 проекта")],
)
def test_parallel_limit_answer_uses_runtime_streams(monkeypatch, count: int, phrase: str) -> None:
    monkeypatch.setattr("app.services.gen_queue.worker_max_parallel", lambda: count)
    answer = parallel_limit_answer(PARALLEL_QUESTION)
    assert answer is not None and phrase in answer


def test_parallel_limit_answer_only_for_limit_questions(monkeypatch) -> None:
    monkeypatch.setattr("app.services.gen_queue.worker_max_parallel", lambda: 4)
    assert parallel_limit_answer("что сейчас в очереди?") is None
    assert parallel_limit_answer("какая модель стоит на сценарии?") is None
    assert parallel_limit_answer("сколько можно запускать параллельно?") is not None


@pytest.mark.asyncio
async def test_empty_model_answer_on_parallel_question_answers_limit(runtime, monkeypatch) -> None:
    monkeypatch.setattr("app.services.gen_queue.worker_max_parallel", lambda: 10)
    runtime.router.responses = [_empty_response()]
    await runtime.queue.accept("empty-par", PARALLEL_QUESTION)
    await runtime.queue.drain()
    saved = _assistant_texts("empty-par")
    assert len(saved) == 1
    assert "до 10 проектов" in saved[0]
    assert _shown(runtime, "empty-par") == saved


@pytest.mark.asyncio
async def test_empty_model_answer_is_never_saved_empty(runtime) -> None:
    runtime.router.answer = ""
    await runtime.queue.accept("empty-any", "добро пожаловать")
    await runtime.queue.drain()
    assert _assistant_texts("empty-any") == [EMPTY_ANSWER_TEXT]
    assert _shown(runtime, "empty-any") == [EMPTY_ANSWER_TEXT]
    tokens = [event for event in runtime.bus.replay("empty-any", 0) if event.type == "token"]
    assert "".join(str(event.payload.get("text")) for event in tokens).strip() == EMPTY_ANSWER_TEXT


@pytest.mark.asyncio
async def test_budget_stop_without_text_says_so(runtime) -> None:
    approval = ApprovalEvent(reason=ApprovalReason.budget_exhausted.value)
    runtime.router.responses = [
        _empty_response(task_status=TaskStatus.waiting_approval, approval=approval),
    ]
    await runtime.queue.accept("empty-budget", "добро пожаловать")
    await runtime.queue.drain()
    assert _assistant_texts("empty-budget") == [EMPTY_BUDGET_TEXT]


@pytest.mark.asyncio
async def test_router_failure_on_parallel_question_answers_limit(runtime, scripted, monkeypatch) -> None:
    monkeypatch.setattr("app.services.gen_queue.worker_max_parallel", lambda: 4)

    async def _fail(*_args, **_kwargs):
        raise RouterExhausted("chat stream error: upstream error (upstream_error)")

    monkeypatch.setattr(scripted, "call", _fail)
    await runtime.queue.accept("fail-par", PARALLEL_QUESTION)
    await runtime.queue.drain()
    saved = _assistant_texts("fail-par")
    assert len(saved) == 1 and "до 4 проектов" in saved[0]


@pytest.mark.asyncio
async def test_router_failure_on_other_question_explains(runtime, scripted, monkeypatch) -> None:
    async def _fail(*_args, **_kwargs):
        raise RouterExhausted("chat stream error: upstream error (upstream_error)")

    monkeypatch.setattr(scripted, "call", _fail)
    await runtime.queue.accept("fail-any", "добро пожаловать")
    await runtime.queue.drain()
    saved = _assistant_texts("fail-any")
    assert len(saved) == 1 and saved[0].startswith("Ответить не получилось.")
