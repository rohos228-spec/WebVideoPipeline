"""Запуски шагов в разных проектах одной parallel_group идут одновременно.

Все шаги группы спрашиваются одной карточкой с суммой и списком проектов,
каждый проходит валидаторы до и после и пишет свою стоимость. Тот же проект дважды или чужое
изменяющее действие в группе — по очереди. Сбой одного не гасит соседей.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from app.orchestrator.agents.runtime import build_runtime, set_runtime
from app.orchestrator.contracts import ActionResult, PlanStep, Risk, TaskStatus
from tests.agents.mocks import FixedPlanner, MockValidators, ScriptedCritic, ScriptedRouter

_RUN = "step.run_step"


class _Registry:
    """step.run_step — платное и разрушительное, как в проде; project.status — чтение."""

    def __init__(self, expect: int) -> None:
        self.expect = expect
        self.calls: list[dict[str, Any]] = []
        self.active = 0
        self.peak = 0
        self.all_in = asyncio.Event()
        self.release = asyncio.Event()
        self.fail_projects: set[int] = set()
        self.raise_projects: set[int] = set()
        self.wait_for_all = True
        self.table = {
            "project.status": {"risk": "none", "read_only": True, "destructive": False, "paid": False},
            _RUN: {"risk": "high", "read_only": False, "destructive": True, "paid": True},
            "music.generate": {"risk": "medium", "read_only": False, "destructive": False, "paid": True},
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

    async def execute(self, name: str, params: dict, task_ctx: dict) -> ActionResult:
        self.calls.append({"name": name, "params": dict(params), "ctx": dict(task_ctx)})
        if name != _RUN:
            return ActionResult(ok=True, data={"name": name}, change_id=None, cost=0.0)
        project = int(params["project_id"])
        self.active += 1
        self.peak = max(self.peak, self.active)
        started = sum(1 for call in self.calls if call["name"] == _RUN)
        if started >= self.expect:
            self.all_in.set()
        try:
            if self.wait_for_all:
                # Шаг ждёт, пока стартуют все: последовательный прогон тут бы завис.
                await asyncio.wait_for(self.all_in.wait(), timeout=2)
            else:
                await asyncio.sleep(0.01)
        finally:
            self.active -= 1
        if project in self.raise_projects:
            raise RuntimeError(f"boom {project}")
        if project in self.fail_projects:
            return ActionResult(ok=False, data={"error": "x"}, change_id=None, cost=0.5)
        return ActionResult(ok=True, data={"project": project}, change_id=f"run-{project}", cost=0.5)


def _run(project: int, group: str | None = "launch", action: str = _RUN) -> PlanStep:
    return PlanStep(
        action=action,
        params={"project_id": project, "step_code": "plan", "allow_auto_chain": True, "ceiling_credits": 100},
        paid=action == _RUN,
        mutating=True,
        risk="high",
        estimated_cost=0.5,
        parallel_group=group,
    )


def _status(project: int) -> PlanStep:
    return PlanStep(action="project.status", params={"project_id": project}, risk="none", parallel_group="reads")


def _runtime(actions: _Registry, validators: MockValidators, steps: list[PlanStep]):
    return build_runtime(
        actions=actions,
        validators=validators,
        router=ScriptedRouter(),
        planner=FixedPlanner(steps),
        critic=ScriptedCritic(["approve"]),
    )


async def _approve_all(runtime: Any, session: str, task_id: int, limit: int = 10) -> list[dict]:
    """Жать «Подтвердить» на каждой карточке, пока задача ждёт. Возвращает карточки."""
    cards: list[dict] = []
    await runtime.supervisor.wait(task_id, timeout=5)
    for _ in range(limit):
        if runtime.tasks.get(task_id)["status"] != TaskStatus.waiting_approval.value:
            break
        opened = [event for event in runtime.bus.replay(session, 0) if event.type == "approval_required"]
        cards.append(dict(opened[-1].payload))
        await runtime.supervisor.grant_approval(session)
        await runtime.supervisor.wait(task_id, timeout=10)
    return cards


def _step_rows(runtime: Any, task_id: int) -> list[dict]:
    return [row for row in runtime.tasks.list_steps(task_id) if row["action_name"] == _RUN]


@pytest.mark.asyncio
async def test_runs_on_three_projects_go_together(token: str) -> None:
    del token
    actions = _Registry(expect=3)
    validators = MockValidators()
    plan = [_status(1), _status(2), _status(3), _run(1), _run(2), _run(3)]
    runtime = _runtime(actions, validators, plan)
    set_runtime(runtime)
    session = "par-three"
    try:
        task_id = await runtime.supervisor.start_task(session, "запусти сценарий у проектов 1..3")
        cards = await _approve_all(runtime, session, task_id)
        # Одна карточка на три запуска, до старта любого из них.
        assert [card["action"] for card in cards] == [_RUN]
        card = cards[0]
        assert card["group"]["projects"] == [1, 2, 3]
        assert card["group"]["positions"] == [3, 4, 5]
        assert [step["action"] for step in card["steps"]] == [_RUN, _RUN, _RUN]
        assert "3 проекта одним подтверждением" in card["title"]
        assert runtime.tasks.get(task_id)["status"] == TaskStatus.done.value
        assert actions.peak == 3
        runs = [call for call in actions.calls if call["name"] == _RUN]
        assert sorted(call["params"]["project_id"] for call in runs) == [1, 2, 3]
        # Каждый шаг получил своё одобрение от общей карточки, не соседское.
        by_project = {call["params"]["project_id"]: call["ctx"]["approval"]["change_id"] for call in runs}
        assert by_project == {
            project: f"{card['change_id']}-p{position}"
            for project, position in zip((1, 2, 3), (3, 4, 5), strict=True)
        }
        rows = _step_rows(runtime, task_id)
        assert [row["status"] for row in rows] == ["done", "done", "done"]
        assert sorted(row["change_id"] for row in rows) == sorted(by_project.values())
        assert float(runtime.tasks.get(task_id)["spend_actions"] or 0) == pytest.approx(1.5)
        for project in (1, 2, 3):
            for phase in ("before", "after"):
                hits = [
                    call
                    for call in validators.calls
                    if call["context"].get("action") == _RUN
                    and call["context"].get("phase") == phase
                    and call["project_id"] == project
                    and not call["context"].get("dry_run")
                ]
                assert hits, (project, phase)
                assert all(call["context"].get("approval", {}).get("granted") for call in hits)
        assert any(call["name"] == "spend_guard" and call["project_id"] == 3 for call in validators.calls)
    finally:
        actions.all_in.set()
        set_runtime(None)


@pytest.mark.asyncio
async def test_same_project_twice_runs_one_after_another(token: str) -> None:
    del token
    actions = _Registry(expect=2)
    actions.wait_for_all = False
    plan = [_status(4), _run(4), _run(4).model_copy(update={"params": {**_run(4).params, "step_code": "script"}})]
    runtime = _runtime(actions, MockValidators(), plan)
    set_runtime(runtime)
    session = "par-same"
    try:
        task_id = await runtime.supervisor.start_task(session, "два шага в одном проекте")
        await _approve_all(runtime, session, task_id)
        assert runtime.tasks.get(task_id)["status"] == TaskStatus.done.value
        assert actions.peak == 1
        assert len([call for call in actions.calls if call["name"] == _RUN]) == 2
    finally:
        set_runtime(None)


@pytest.mark.asyncio
async def test_foreign_mutating_step_in_group_keeps_order(token: str) -> None:
    del token
    actions = _Registry(expect=2)
    actions.wait_for_all = False
    rename = PlanStep(
        action="music.generate",
        params={"project_id": 6, "prompt": "x"},
        paid=True,
        estimated_cost=0.1,
        mutating=True,
        risk="medium",
        parallel_group="launch",
    )
    plan = [_status(5), _status(6), _run(5), rename]
    runtime = _runtime(actions, MockValidators(), plan)
    set_runtime(runtime)
    session = "par-foreign"
    try:
        task_id = await runtime.supervisor.start_task(session, "запуск и переименование")
        await _approve_all(runtime, session, task_id)
        assert runtime.tasks.get(task_id)["status"] == TaskStatus.done.value
        assert [call["name"] for call in actions.calls][-2:] == [_RUN, "music.generate"]
        assert actions.peak == 1
    finally:
        set_runtime(None)


@pytest.mark.asyncio
async def test_null_group_is_not_grouped_automatically(token: str) -> None:
    del token
    actions = _Registry(expect=2)
    actions.wait_for_all = False
    plan = [_status(7), _status(8), _run(7, group=None), _run(8, group=None)]
    runtime = _runtime(actions, MockValidators(), plan)
    set_runtime(runtime)
    session = "par-null"
    try:
        task_id = await runtime.supervisor.start_task(session, "два проекта без группы")
        await _approve_all(runtime, session, task_id)
        assert runtime.tasks.get(task_id)["status"] == TaskStatus.done.value
        assert actions.peak == 1
    finally:
        set_runtime(None)


@pytest.mark.asyncio
async def test_one_failing_run_does_not_stop_the_others(token: str) -> None:
    del token
    actions = _Registry(expect=3)
    actions.raise_projects = {2}
    plan = [_status(1), _status(2), _status(3), _run(1), _run(2), _run(3)]
    runtime = _runtime(actions, MockValidators(), plan)
    set_runtime(runtime)
    session = "par-fail"
    try:
        task_id = await runtime.supervisor.start_task(session, "три проекта, один падает")
        await _approve_all(runtime, session, task_id)
        assert actions.peak == 3
        task = runtime.tasks.get(task_id)
        assert task["status"] == TaskStatus.failed.value
        rows = {int(json.loads(row["params_json"])["project_id"]): row for row in _step_rows(runtime, task_id)}
        assert rows[1]["status"] == "done" and rows[3]["status"] == "done"
        assert rows[2]["status"] == "failed"
        assert float(task["spend_actions"] or 0) == pytest.approx(1.0)
        finished = [event.payload for event in runtime.bus.replay(session, 0) if event.type == "step_finished"]
        assert sum(1 for item in finished if item.get("action") == _RUN and item.get("ok")) == 2
    finally:
        actions.all_in.set()
        set_runtime(None)


@pytest.mark.asyncio
async def test_rejected_run_records_neighbours_and_their_spend(token: str) -> None:
    del token
    actions = _Registry(expect=2)
    actions.fail_projects = {1}
    plan = [_status(1), _status(2), _run(1), _run(2)]
    runtime = _runtime(actions, MockValidators(), plan)
    set_runtime(runtime)
    session = "par-reject"
    try:
        task_id = await runtime.supervisor.start_task(session, "два проекта, один ok=False")
        await _approve_all(runtime, session, task_id)
        assert actions.peak == 2
        task = runtime.tasks.get(task_id)
        assert task["status"] == TaskStatus.failed.value
        statuses = sorted(row["status"] for row in _step_rows(runtime, task_id))
        assert statuses == ["done", "failed"]
        assert float(task["spend_actions"] or 0) == pytest.approx(1.0)
    finally:
        actions.all_in.set()
        set_runtime(None)


def test_planner_is_told_to_group_multi_project_launches() -> None:
    from app.orchestrator.agents.planner import _CATALOG_RULES

    rules = " ".join(_CATALOG_RULES)
    assert "нескольких проектах" in rules and "одной общей parallel_group" in rules
