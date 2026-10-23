"""Планировщик и критик — один объект на все чаты. Их ``last_*`` не должны течь между задачами.

Две сессии планируют одновременно. Ответ роутера для обеих приходит в одной итерации
цикла: внутренняя задача второй успевает перезаписать ``planner.last_response`` и
``last_from_code`` раньше, чем супервизор первой их прочтёт. Без снимка на задачу
первая получает чужой ответ: чужое «бюджет исчерпан» (текст уходит в её чат),
чужой признак «план собран кодом» и чужую причину пустого плана.
"""

from __future__ import annotations

import asyncio
import json
import socket
from types import SimpleNamespace
from typing import Any

import pytest

from app.orchestrator.agents.critic import RouterCritic
from app.orchestrator.agents.planner import RouterPlanner
from app.orchestrator.agents.runtime import build_runtime, set_runtime
from app.orchestrator.contracts import ApprovalEvent, LLMResponse, PlanStep, TaskStatus
from tests.agents.mocks import MockActions, MockValidators, ScriptedCritic, ScriptedRouter

_PLAN_A = json.dumps({"steps": [{"action": "project.status", "params": {"project_id": 5}}]})
_BUDGET_TEXT = "лимит внутренних трат задачи Б исчерпан"


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ни одного внешнего соединения: платных вызовов тут быть не может."""

    def _blocked(self: socket.socket, address: Any) -> None:
        host = address[0] if isinstance(address, tuple) else str(address)
        if host not in ("127.0.0.1", "localhost", "::1"):
            raise AssertionError(f"network call in test: {address}")
        return _orig(self, address)

    _orig = socket.socket.connect
    monkeypatch.setattr(socket.socket, "connect", _blocked)


def _reply(text: str, *, budget: bool = False) -> LLMResponse:
    extra: dict[str, Any] = {}
    if budget:
        extra = {
            "task_status": TaskStatus.waiting_approval,
            "approval": ApprovalEvent(reason="budget_exhausted", payload={"spent_usd": 10}),
        }
    return LLMResponse(
        text=text,
        call_id="gated",
        model="scripted",
        provider="52pay",
        tier="strong",
        finish_reason="stop",
        **extra,
    )


class _GatedRouter(ScriptedRouter):
    """Все вызовы ждут один шлюз; ответ выбирается по тексту цели в сообщении."""

    def __init__(self, replies: dict[str, LLMResponse]) -> None:
        super().__init__()
        self.replies = replies
        self.gate = asyncio.Event()
        self.waiting = 0
        self.seen_tasks: list[Any] = []

    async def call(self, tier: str, messages: list[dict[str, Any]], stream: bool = False, **kwargs: Any) -> LLMResponse:
        user = " ".join(str(item.get("content") or "") for item in messages if item.get("role") == "user")
        self.seen_tasks.append(kwargs.get("task_id"))
        self.waiting += 1
        await self.gate.wait()
        for key, reply in self.replies.items():
            if key in user:
                return reply
        raise AssertionError(f"unexpected call: {user[:80]}")


async def _both_waiting(router: _GatedRouter, count: int = 2) -> None:
    for _ in range(400):
        if router.waiting >= count:
            return
        await asyncio.sleep(0.005)
    raise AssertionError(f"only {router.waiting} planner calls reached the router")


def _events(runtime: Any, session: str) -> list[Any]:
    return list(runtime.bus.replay(session, 0))


@pytest.mark.asyncio
async def test_budget_stop_of_one_chat_does_not_leak_into_another(token: str) -> None:
    del token
    router = _GatedRouter({"цель-А": _reply(_PLAN_A), "цель-Б": _reply(_BUDGET_TEXT, budget=True)})
    runtime = build_runtime(
        actions=MockActions(),
        validators=MockValidators(),
        router=router,
        planner=RouterPlanner(router),
        critic=ScriptedCritic(["approve"] * 4),
    )
    set_runtime(runtime)
    try:
        task_a = await runtime.supervisor.start_task("race-a", "цель-А статус проекта 5")
        task_b = await runtime.supervisor.start_task("race-b", "цель-Б статус проекта 6")
        await _both_waiting(router)
        router.gate.set()
        await runtime.supervisor.wait(task_a, timeout=10)
        await runtime.supervisor.wait(task_b, timeout=10)

        row_a = runtime.tasks.get(task_a)
        row_b = runtime.tasks.get(task_b)
        assert row_b["status"] == TaskStatus.waiting_approval.value
        assert runtime.supervisor._block_reason.get(task_b) == "budget_exhausted"
        # Чат А не получил чужой бюджетный стоп и его текст.
        assert runtime.supervisor._block_reason.get(task_a) != "budget_exhausted"
        assert row_a["status"] == TaskStatus.done.value, row_a
        texts_a = [str(event.payload.get("text") or "") for event in _events(runtime, "race-a")]
        assert not any(_BUDGET_TEXT in text for text in texts_a)
    finally:
        router.gate.set()
        set_runtime(None)


@pytest.mark.asyncio
async def test_own_budget_stop_is_not_lost_to_a_neighbour(token: str) -> None:
    """Обратный порядок: бюджет кончился у первой задачи, сосед отвечает обычным планом."""
    del token
    router = _GatedRouter({"цель-А": _reply(_BUDGET_TEXT, budget=True), "цель-Б": _reply(_PLAN_A)})
    runtime = build_runtime(
        actions=MockActions(),
        validators=MockValidators(),
        router=router,
        planner=RouterPlanner(router),
        critic=ScriptedCritic(["approve"] * 4),
    )
    set_runtime(runtime)
    try:
        task_a = await runtime.supervisor.start_task("race2-a", "цель-А статус проекта 5")
        task_b = await runtime.supervisor.start_task("race2-b", "цель-Б статус проекта 6")
        await _both_waiting(router)
        router.gate.set()
        await runtime.supervisor.wait(task_a, timeout=10)
        await runtime.supervisor.wait(task_b, timeout=10)
        assert runtime.supervisor._block_reason.get(task_a) == "budget_exhausted"
        assert runtime.tasks.get(task_a)["status"] == TaskStatus.waiting_approval.value
        assert runtime.tasks.get(task_b)["status"] == TaskStatus.done.value
    finally:
        router.gate.set()
        set_runtime(None)


@pytest.mark.asyncio
async def test_code_built_undo_plan_flag_stays_with_its_task(token: str) -> None:
    """«Верни как было» собрано кодом у А; Б в ту же итерацию спрашивает модель.

    Флаг ``last_from_code`` должен остаться у А: её план не идёт к критику.
    """
    del token
    gate = asyncio.Event()
    undo_step = PlanStep(action="project.patch_settings", params={"project_id": 5, "x": 1},
                         mutating=True, risk="medium")

    async def _undo(goal: str, task_id: Any) -> Any:
        await gate.wait()
        if "верни" in goal:
            return SimpleNamespace(steps=[undo_step.model_dump()], reason="вернуть правку")
        return None

    router = _GatedRouter({"цель-Б": _reply(_PLAN_A)})
    router.gate = gate
    critic_seen: list[Any] = []

    class _Critic(ScriptedCritic):
        async def review(self, plan: list[PlanStep], context: str = "", **kwargs: Any) -> str:
            critic_seen.append(kwargs.get("task_id"))
            return "approve"

    runtime = build_runtime(
        actions=MockActions(),
        validators=MockValidators(),
        router=router,
        planner=RouterPlanner(router, undo=_undo),
        critic=_Critic([]),
    )
    set_runtime(runtime)
    try:
        task_a = await runtime.supervisor.start_task("race3-a", "верни как было")
        task_b = await runtime.supervisor.start_task("race3-b", "цель-Б статус проекта 6")
        for _ in range(200):
            await asyncio.sleep(0.005)
        gate.set()
        settled = {"waiting_approval", "done", "failed", "cancelled"}
        for _ in range(1000):
            if all(runtime.tasks.get(t)["status"] in settled for t in (task_a, task_b)):
                break
            await asyncio.sleep(0.01)
        assert runtime.tasks.get(task_b)["status"] == TaskStatus.done.value
        assert int(task_b) in {int(item) for item in critic_seen}
        # План отката собран кодом: критик его не видит.
        assert str(task_a) not in {str(item) for item in critic_seen}, critic_seen
    finally:
        gate.set()
        set_runtime(None)


def test_router_critic_keeps_last_response_contract() -> None:
    """Снимок берётся с тех же атрибутов, что выставляют боевые планировщик и критик."""
    planner = RouterPlanner(ScriptedRouter())
    critic = RouterCritic(ScriptedRouter())
    for name in ("last_response", "last_reason", "last_from_code", "last_parse_failed"):
        assert hasattr(planner, name)
    assert hasattr(critic, "last_response")


async def _snap(planner_or_critic: Any, store: dict, task_id: int, coro: Any) -> Any:
    from app.orchestrator.agents.supervisor import Supervisor

    return await Supervisor._snapshot_agent(None, store, planner_or_critic, task_id, coro)


@pytest.mark.asyncio
async def test_model_plan_is_not_marked_code_built_by_a_concurrent_undo() -> None:
    """Порядок из ревью: Б прошла undo и ждёт модель; А делает откат кодом и заканчивает;
    затем модель отвечает Б. Снимок Б не должен нести last_from_code=True от А."""
    gate_router = asyncio.Event()
    gate_undo = asyncio.Event()

    class _Router:
        async def call(self, tier: str, messages: list, stream: bool = False, **kwargs: Any) -> LLMResponse:
            await gate_router.wait()
            return _reply(_PLAN_A)

    async def _undo(goal: str, task_id: Any) -> Any:
        if "верни" in goal:
            await gate_undo.wait()
            return SimpleNamespace(steps=[{"action": "project.patch_settings", "params": {"project_id": 5}}],
                                   reason="r")
        return None

    planner = RouterPlanner(_Router(), undo=_undo)
    store: dict = {}
    task_b = asyncio.create_task(_snap(planner, store, 2, planner.make_plan("цель-Б статус", task_id=2, run_id="r2")))
    await asyncio.sleep(0.05)
    task_a = asyncio.create_task(_snap(planner, store, 1, planner.make_plan("верни как было", task_id=1)))
    await asyncio.sleep(0.01)
    gate_undo.set()
    await task_a
    gate_router.set()
    await task_b
    assert store[1]["last_from_code"] is True
    assert store[2]["last_from_code"] is False
    assert store[2]["last_response"] is not None


@pytest.mark.asyncio
async def test_undo_answer_resets_parse_failed_left_by_a_previous_call() -> None:
    planner = RouterPlanner(_GatedRouter({}), undo=lambda goal, task_id: SimpleNamespace(steps=[], reason="нечего"))
    planner.last_parse_failed = True
    store: dict = {}
    assert await _snap(planner, store, 3, planner.make_plan("верни как было", task_id=3)) == []
    assert store[3]["last_parse_failed"] is False
    assert store[3]["last_from_code"] is True


@pytest.mark.asyncio
async def test_critic_early_verdict_does_not_carry_a_neighbours_response() -> None:
    """Критик отклоняет удаление без модели. Если сосед выставил last_response за время
    retrieve, снимок отклонённой задачи не должен нести чужой (бюджетный) ответ."""
    gate = asyncio.Event()

    class _Rag:
        async def retrieve(self, query: str, corpora: list, top_k: int) -> list:
            await gate.wait()
            return []

    critic = RouterCritic(ScriptedRouter(), _Rag())
    store: dict = {}
    deletion = [PlanStep(action="frame.delete", params={"project_id": 5, "frame": 1}, mutating=True)]
    job = asyncio.create_task(_snap(critic, store, 4, critic.review(deletion, task_id=4)))
    await asyncio.sleep(0.02)
    critic.last_response = _reply(_BUDGET_TEXT, budget=True)  # сосед ответил, пока мы ждали retrieve
    gate.set()
    assert await job == "reject"
    assert store[4]["last_response"] is None


@pytest.mark.asyncio
async def test_cancelled_planner_call_is_not_charged_a_neighbours_cost(token: str) -> None:
    """Отмена вызова: ``last_response`` общего роутера — ответ соседней задачи (5 USD).
    Его стоимость не должна садиться на отменённую задачу."""
    del token
    hold = asyncio.Event()

    class _Router(ScriptedRouter):
        async def call(self, tier: str, messages: list, stream: bool = False, **kwargs: Any) -> LLMResponse:
            self.call_started.set()
            await hold.wait()
            return _reply(_PLAN_A)

    router = _Router()
    planner = RouterPlanner(router)
    neighbour = _reply(_PLAN_A).model_copy(update={"cost_usd": 5.0})
    router.last_response = neighbour
    runtime = build_runtime(actions=MockActions(), validators=MockValidators(), router=router,
                            planner=planner, critic=ScriptedCritic(["approve"]))
    set_runtime(runtime)
    try:
        task_id = await runtime.supervisor.start_task("cost-cancel", "цель-А")
        await router.call_started.wait()
        runtime.supervisor._runs[task_id].cancel()
        try:
            await runtime.supervisor.wait(task_id, timeout=5)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass
        assert float(runtime.tasks.get(task_id)["spend_internal"] or 0) < 5.0
    finally:
        hold.set()
        set_runtime(None)
