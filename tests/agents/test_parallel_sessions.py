"""Несколько чатов с запусками одновременно: карточки и ответы не путаются, двойного запуска нет.

* три сессии, у каждой своя карточка на свои проекты; «Подтвердить» приходит не по порядку;
* двойной клик «Подтвердить» (два гранта разом) не запускает проекты дважды;
* рестарт процесса посреди шести платных запусков: новые запуски не стартуют,
  задача ждёт решения человека, карточка соседнего чата так и ждёт своего гранта.

Только фейки в памяти, сети нет.
"""

from __future__ import annotations

import asyncio
import socket
from typing import Any

import pytest

from app.orchestrator.agents.runtime import build_runtime, set_runtime
from app.orchestrator.contracts import ActionResult, PlanStep, Risk, TaskStatus
from tests.agents.mocks import MockValidators, ScriptedRouter

_RUN = "step.run_step"


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    orig = socket.socket.connect

    def _blocked(self: socket.socket, address: Any) -> None:
        host = address[0] if isinstance(address, tuple) else str(address)
        if host not in ("127.0.0.1", "localhost", "::1"):
            raise AssertionError(f"network call in test: {address}")
        return orig(self, address)

    monkeypatch.setattr(socket.socket, "connect", _blocked)


class _Launch:
    def __init__(self, *, hold: bool = False) -> None:
        self.calls: list[dict[str, Any]] = []
        self.active = 0
        self.hold = hold
        self.release = asyncio.Event()
        self.table = {
            "project.status": {"risk": "none", "read_only": True, "destructive": False, "paid": False},
            _RUN: {"risk": "high", "read_only": False, "destructive": True, "paid": True},
        }

    def metadata_registry(self) -> dict[str, dict[str, object]]:
        return {name: self.metadata_of(name) for name in self.table}

    def metadata_of(self, name: str) -> dict[str, object]:
        if name not in self.table:
            raise KeyError(name)
        return {"name": name, **self.table[name]}

    def human_plan_required(self, name: str) -> bool:
        return name == _RUN

    def risk_of(self, name: str) -> Risk:
        return Risk(str(self.metadata_of(name)["risk"]))

    def runs(self) -> list[dict[str, Any]]:
        return [call for call in self.calls if call["name"] == _RUN]

    async def execute(self, name: str, params: dict, task_ctx: dict) -> ActionResult:
        self.calls.append({"name": name, "params": dict(params), "ctx": dict(task_ctx)})
        if name != _RUN:
            return ActionResult(ok=True, data={"name": name}, change_id=None, cost=0.0)
        self.active += 1
        try:
            if self.hold:
                await asyncio.wait_for(self.release.wait(), timeout=15)
            else:
                await asyncio.sleep(0.01)
        finally:
            self.active -= 1
        project = int(params["project_id"])
        return ActionResult(ok=True, data={"project": project}, change_id=f"run-{project}", cost=0.5)


def _plan(projects: list[int]) -> list[PlanStep]:
    reads = [PlanStep(action="project.status", params={"project_id": p}, risk="none", parallel_group="reads")
             for p in projects]
    runs = [
        PlanStep(
            action=_RUN,
            params={"project_id": p, "step_code": "img", "allow_auto_chain": True, "ceiling_credits": 100},
            paid=True,
            mutating=True,
            risk="high",
            estimated_cost=0.5,
            parallel_group="launch",
        )
        for p in projects
    ]
    return reads + runs


class _GoalPlanner:
    """Свой план на каждую цель: «проекты 5 6» → запуск 5 и 6."""

    def __init__(self, plans: dict[str, list[int]]) -> None:
        self.plans = plans

    async def make_plan(self, goal: str, context: str = "", **kwargs: Any) -> list[PlanStep]:
        del context, kwargs
        await asyncio.sleep(0)
        for key, projects in self.plans.items():
            if key in goal:
                return _plan(projects)
        raise AssertionError(goal)


class _Approve:
    async def review(self, plan: list[PlanStep], context: str = "", **kwargs: Any) -> str:
        del plan, context, kwargs
        await asyncio.sleep(0)
        return "approve"


_PLANS = {"альфа": [5, 6], "бета": [7, 8], "гамма": [9, 10]}
_SESSIONS = {"альфа": "s-alpha", "бета": "s-beta", "гамма": "s-gamma"}


def _runtime(actions: _Launch) -> Any:
    return build_runtime(
        actions=actions,
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=_GoalPlanner(_PLANS),
        critic=_Approve(),
    )


def _cards(runtime: Any, session: str) -> list[dict]:
    return [dict(event.payload) for event in runtime.bus.replay(session, 0) if event.type == "approval_required"]


async def _until(predicate: Any, what: str, limit: float = 5.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + limit
    while not predicate():
        if loop.time() > deadline:
            raise AssertionError(f"timeout: {what}")
        await asyncio.sleep(0.01)


async def _start_all(runtime: Any) -> dict[str, int]:
    started = await asyncio.gather(*(
        runtime.supervisor.start_task(_SESSIONS[key], f"запусти проекты {key}") for key in _PLANS
    ))
    tasks = dict(zip(_PLANS, started, strict=True))
    for task_id in tasks.values():
        await runtime.supervisor.wait(task_id, timeout=5)
        assert runtime.tasks.get(task_id)["status"] == TaskStatus.waiting_approval.value
    return tasks


@pytest.mark.asyncio
async def test_three_chats_out_of_order_grants_keep_cards_apart(token: str) -> None:
    del token
    actions = _Launch()
    runtime = _runtime(actions)
    set_runtime(runtime)
    try:
        tasks = await _start_all(runtime)
        cards = {key: _cards(runtime, _SESSIONS[key]) for key in _PLANS}
        for key, projects in _PLANS.items():
            assert len(cards[key]) == 1
            assert cards[key][0]["group"]["projects"] == projects
            assert cards[key][0]["task_id"] == tasks[key]
        # Гранты не по порядку: гамма, альфа, бета.
        for key in ("гамма", "альфа", "бета"):
            await runtime.supervisor.grant_approval(_SESSIONS[key])
        for task_id in tasks.values():
            await runtime.supervisor.wait(task_id, timeout=10)
        for key, projects in _PLANS.items():
            task_id = tasks[key]
            assert runtime.tasks.get(task_id)["status"] == TaskStatus.done.value
            mine = [call for call in actions.runs() if call["ctx"].get("task_id") == task_id]
            assert sorted(int(call["params"]["project_id"]) for call in mine) == projects
            # Каждый запуск несёт одобрение своей карточки, не соседской.
            card_id = cards[key][0]["change_id"]
            assert all(str(call["ctx"]["approval"]["change_id"]).startswith(card_id) for call in mine)
            # В ленте сессии нет событий чужих задач.
            others = {tasks[k] for k in _PLANS if k != key}
            foreign = [e for e in runtime.bus.replay(_SESSIONS[key], 0) if e.task_id in others]
            assert foreign == []
        assert sorted(int(call["params"]["project_id"]) for call in actions.runs()) == [5, 6, 7, 8, 9, 10]
    finally:
        set_runtime(None)


@pytest.mark.asyncio
async def test_double_grant_does_not_launch_twice(token: str) -> None:
    del token
    actions = _Launch()
    runtime = _runtime(actions)
    set_runtime(runtime)
    try:
        tasks = await _start_all(runtime)
        session = _SESSIONS["альфа"]
        results = await asyncio.gather(
            runtime.supervisor.grant_approval(session),
            runtime.supervisor.grant_approval(session),
            return_exceptions=True,
        )
        assert any(not isinstance(item, BaseException) for item in results)
        await runtime.supervisor.wait(tasks["альфа"], timeout=10)
        assert sorted(int(call["params"]["project_id"]) for call in actions.runs()) == [5, 6]
        # Соседние карточки не тронуты чужим грантом.
        for key in ("бета", "гамма"):
            assert runtime.tasks.get(tasks[key])["status"] == TaskStatus.waiting_approval.value
    finally:
        set_runtime(None)


@pytest.mark.asyncio
async def test_restart_mid_parallel_launch_does_not_relaunch(token: str) -> None:
    del token
    old_actions = _Launch(hold=True)
    runtime = _runtime(old_actions)
    set_runtime(runtime)
    fresh_actions = _Launch()
    try:
        tasks = await _start_all(runtime)
        await runtime.supervisor.grant_approval(_SESSIONS["альфа"])
        await _until(lambda: old_actions.active == 2, "two runs in flight")

        # «Рестарт»: новый процесс на той же базе, старый больше ничего не делает.
        fresh = _runtime(fresh_actions)
        set_runtime(fresh)
        await fresh.supervisor.recover_after_restart()
        alpha = tasks["альфа"]
        live = fresh.supervisor._runs.get(alpha)
        if live is not None and not live.done():
            await asyncio.wait_for(asyncio.shield(live), timeout=5)
        assert fresh_actions.runs() == []
        assert fresh.tasks.get(alpha)["status"] == TaskStatus.waiting_approval.value
        assert fresh.supervisor._block_reason.get(alpha) == "paid_outcome_unknown"
        for key in ("бета", "гамма"):
            assert fresh.tasks.get(tasks[key])["status"] == TaskStatus.waiting_approval.value
        # Карточка беты после рестарта запускает только свои проекты.
        await fresh.supervisor.adopt_after_restart(_SESSIONS["бета"], task_id=tasks["бета"])
        await fresh.supervisor.grant_approval(_SESSIONS["бета"])
        await fresh.supervisor.wait(tasks["бета"], timeout=10)
        assert fresh.tasks.get(tasks["бета"])["status"] == TaskStatus.done.value
        assert sorted(int(call["params"]["project_id"]) for call in fresh_actions.runs()) == [7, 8]
    finally:
        old_actions.release.set()
        set_runtime(None)
