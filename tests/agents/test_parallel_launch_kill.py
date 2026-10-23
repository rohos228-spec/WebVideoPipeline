"""«Запусти проекты 5 6 7 8 9 10 до картинок»: одна карточка, шесть запусков разом, и стопы.

* одна карточка на шесть запусков, все шесть идут одновременно;
* «аварийный стоп» ставит stop-ключ каждому проекту, чей шаг этой задачи ещё идёт
  (у задачи нет одного project_id, иначе генерация в проектах продолжалась бы);
* чужой stop одного проекта (#7) kill не присваивает и при возобновлении не снимает;
* «стоп» другой сессии не трогает эту.

Все действия — фейки в памяти, сети нет.
"""

from __future__ import annotations

import asyncio
import socket
from typing import Any

import pytest

from app.orchestrator.agents.runtime import build_runtime, set_runtime
from app.orchestrator.contracts import ActionResult, PlanStep, Risk, TaskStatus
from app.services.step_cancel import clear_stop, is_stop_requested, request_stop
from tests.agents.mocks import FixedPlanner, MockValidators, ScriptedCritic, ScriptedRouter

_RUN = "step.run_step"
_PROJECTS = [5, 6, 7, 8, 9, 10]


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    orig = socket.socket.connect

    def _blocked(self: socket.socket, address: Any) -> None:
        host = address[0] if isinstance(address, tuple) else str(address)
        if host not in ("127.0.0.1", "localhost", "::1"):
            raise AssertionError(f"network call in test: {address}")
        return orig(self, address)

    monkeypatch.setattr(socket.socket, "connect", _blocked)


@pytest.fixture(autouse=True)
def _clean_stops() -> Any:
    from app.services.step_cancel import stop_flag_path

    def _wipe() -> None:
        for project in _PROJECTS:
            clear_stop(project)
            flag = stop_flag_path(project)
            flag.with_name(flag.name + ".orch").unlink(missing_ok=True)

    _wipe()
    yield
    _wipe()


class _Launch:
    """step.run_step платный и разрушительный, как в проде; шаг ждёт ``release``."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.active = 0
        self.peak = 0
        self.release = asyncio.Event()
        self.instant: set[int] = set()
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

    def started(self) -> list[int]:
        return [int(call["params"]["project_id"]) for call in self.calls if call["name"] == _RUN]

    async def execute(self, name: str, params: dict, task_ctx: dict) -> ActionResult:
        self.calls.append({"name": name, "params": dict(params), "ctx": dict(task_ctx)})
        if name != _RUN:
            return ActionResult(ok=True, data={"name": name}, change_id=None, cost=0.0)
        self.active += 1
        self.peak = max(self.peak, self.active)
        project = int(params["project_id"])
        try:
            if project not in self.instant:
                await asyncio.wait_for(self.release.wait(), timeout=10)
        finally:
            self.active -= 1
        return ActionResult(ok=True, data={"project": project}, change_id=f"run-{project}", cost=0.5)


def _run(project: int) -> PlanStep:
    return PlanStep(
        action=_RUN,
        params={"project_id": project, "step_code": "img", "allow_auto_chain": True, "ceiling_credits": 100},
        paid=True,
        mutating=True,
        risk="high",
        estimated_cost=0.5,
        parallel_group="launch",
    )


def _plan(projects: list[int]) -> list[PlanStep]:
    reads = [PlanStep(action="project.status", params={"project_id": p}, risk="none", parallel_group="reads")
             for p in projects]
    return reads + [_run(p) for p in projects]


def _runtime(actions: _Launch, projects: list[int]) -> Any:
    return build_runtime(
        actions=actions,
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=FixedPlanner(_plan(projects)),
        critic=ScriptedCritic(["approve"] * 4),
    )


async def _until(predicate: Any, what: str, limit: float = 5.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + limit
    while not predicate():
        if loop.time() > deadline:
            raise AssertionError(f"timeout: {what}")
        await asyncio.sleep(0.01)


async def _launch_all(runtime: Any, actions: _Launch, session: str, projects: list[int]) -> tuple[int, dict]:
    task_id = await runtime.supervisor.start_task(session, "запусти проекты 5 6 7 8 9 10 до картинок")
    await runtime.supervisor.wait(task_id, timeout=5)
    assert runtime.tasks.get(task_id)["status"] == TaskStatus.waiting_approval.value
    cards = [event.payload for event in runtime.bus.replay(session, 0) if event.type == "approval_required"]
    assert len(cards) == 1
    await runtime.supervisor.grant_approval(session)
    await _until(lambda: actions.active == len(projects), "all runs in flight")
    return task_id, dict(cards[0])


@pytest.mark.asyncio
async def test_one_card_six_parallel_runs(token: str) -> None:
    del token
    actions = _Launch()
    runtime = _runtime(actions, _PROJECTS)
    set_runtime(runtime)
    try:
        task_id, card = await _launch_all(runtime, actions, "par6", _PROJECTS)
        assert card["group"]["projects"] == _PROJECTS
        assert actions.peak == 6
        actions.release.set()
        await runtime.supervisor.wait(task_id, timeout=10)
        assert runtime.tasks.get(task_id)["status"] == TaskStatus.done.value
        assert sorted(actions.started()) == _PROJECTS
        assert float(runtime.tasks.get(task_id)["spend_actions"] or 0) == pytest.approx(3.0)
    finally:
        actions.release.set()
        set_runtime(None)


@pytest.mark.asyncio
async def test_kill_switch_stops_every_project_of_the_launch(token: str) -> None:
    del token
    actions = _Launch()
    runtime = _runtime(actions, _PROJECTS)
    set_runtime(runtime)
    session = "par6-kill"
    try:
        task_id, _card = await _launch_all(runtime, actions, session, _PROJECTS)
        assert runtime.tasks.get(task_id)["project_id"] is None
        job = runtime.supervisor.request_kill(session)
        assert job is not None
        await job
        kill = next(event for event in runtime.bus.replay(session, 0) if event.type == "kill_switch")
        # Каждый из шести проектов получил stop-ключ: воркер остановит генерацию в каждом.
        assert [p for p in _PROJECTS if not is_stop_requested(p)] == []
        assert kill.payload["stop_key_set"] is True
        assert kill.payload["stop_key"] != "pending"
        actions.release.set()
        await _until(lambda: actions.active == 0, "runs settled")
        await asyncio.sleep(0.05)
        # Новых запусков после kill нет.
        assert sorted(actions.started()) == _PROJECTS
        assert runtime.tasks.get(task_id)["status"] in {
            TaskStatus.waiting_approval.value,
            TaskStatus.cancelled.value,
            TaskStatus.failed.value,
        }
    finally:
        actions.release.set()
        set_runtime(None)


@pytest.mark.asyncio
async def test_kill_does_not_take_over_a_foreign_stop(token: str) -> None:
    """#7 остановил человек («останови проект 7»). Kill задачи этот ключ не присваивает:
    при снятии своих ключей stop #7 остаётся, а ключи 5, 6, 8, 9, 10 снимаются."""
    del token
    actions = _Launch()
    runtime = _runtime(actions, _PROJECTS)
    set_runtime(runtime)
    session = "par6-foreign"
    try:
        task_id, _card = await _launch_all(runtime, actions, session, _PROJECTS)
        request_stop(7, reason="user_stop")
        job = runtime.supervisor.request_kill(session)
        await job
        assert all(is_stop_requested(p) for p in _PROJECTS)
        runtime.supervisor._clear_project_stop(task_id)
        assert is_stop_requested(7)
        assert [p for p in _PROJECTS if p != 7 and is_stop_requested(p)] == []
    finally:
        actions.release.set()
        set_runtime(None)


@pytest.mark.asyncio
async def test_kill_in_one_chat_leaves_another_chat_running(token: str) -> None:
    del token
    actions = _Launch()
    runtime = _runtime(actions, [5, 6, 7])
    set_runtime(runtime)
    try:
        await _launch_all(runtime, actions, "chat-a", [5, 6, 7])
        # Вторая сессия того же процесса делит супервизор и планировщик.
        runtime.supervisor.planner = FixedPlanner(_plan([8, 9, 10]))
        task_b = await runtime.supervisor.start_task("chat-b", "запусти проекты 8 9 10")
        await runtime.supervisor.wait(task_b, timeout=5)
        assert runtime.tasks.get(task_b)["status"] == TaskStatus.waiting_approval.value
        job = runtime.supervisor.request_kill("chat-a")
        await job
        assert all(is_stop_requested(p) for p in (5, 6, 7))
        assert not any(is_stop_requested(p) for p in (8, 9, 10))
        assert runtime.tasks.get(task_b)["status"] == TaskStatus.waiting_approval.value
        cards_a = [e.payload for e in runtime.bus.replay("chat-a", 0) if e.type == "approval_required"]
        cards_b = [e.payload for e in runtime.bus.replay("chat-b", 0) if e.type == "approval_required"]
        assert [c["group"]["projects"] for c in cards_a if c.get("group")] == [[5, 6, 7]]
        assert [c["group"]["projects"] for c in cards_b if c.get("group")] == [[8, 9, 10]]
        # Карточка Б работает после kill сессии А: её запуски идут и доходят до конца.
        actions.release.set()
        await runtime.supervisor.grant_approval("chat-b")
        await runtime.supervisor.wait(task_b, timeout=10)
        assert runtime.tasks.get(task_b)["status"] == TaskStatus.done.value
        assert sorted(actions.started()) == [5, 6, 7, 8, 9, 10]
    finally:
        actions.release.set()
        set_runtime(None)


@pytest.mark.asyncio
async def test_chat_phrase_kill_switch_stops_every_project(token: str) -> None:
    """Тот же стоп, но репликой «аварийный стоп» через очередь чата."""
    del token
    actions = _Launch()
    runtime = _runtime(actions, _PROJECTS)
    set_runtime(runtime)
    session = "par6-phrase"
    try:
        await _launch_all(runtime, actions, session, _PROJECTS)
        await runtime.queue.accept(session, "аварийный стоп")
        await runtime.queue.drain(timeout=5)
        assert [p for p in _PROJECTS if not is_stop_requested(p)] == []
        classified = [e.payload for e in runtime.bus.replay(session, 0) if e.type == "classification"]
        assert classified and classified[-1]["kill_switch"] is True
    finally:
        actions.release.set()
        set_runtime(None)


@pytest.mark.asyncio
async def test_stop_one_project_in_the_same_chat_does_not_wait_for_the_launch(token: str) -> None:
    """«Останови проект 7», пока идёт запуск 5..10 в этом же чате.

    Раньше реплика вставала в очередь за задачей запуска и выполнялась после конца всех
    шести генераций, то есть #7 не останавливался. Теперь стоп #7 уходит сразу, остальные
    пять идут дальше, задача запуска не отменяется.
    """
    del token
    actions = _Launch()
    actions.table["project.stop"] = {"risk": "low", "read_only": False, "destructive": False, "paid": False}
    runtime = _runtime(actions, _PROJECTS)
    set_runtime(runtime)
    session = "par6-stop7"
    try:
        task_id, _ = await _launch_all(runtime, actions, session, _PROJECTS)
        await runtime.queue.accept(session, "останови проект 7")
        await runtime.queue.drain(timeout=5)
        stops = [call for call in actions.calls if call["name"] == "project.stop"]
        assert [int(call["params"]["project_id"]) for call in stops] == [7]
        assert [row for row in runtime.tasks.list_open() if row["status"] == "queued"] == []
        assert runtime.tasks.get(task_id)["status"] == TaskStatus.executing.value
        assert actions.active == 6
        replies = [e.payload for e in runtime.bus.replay(session, 0) if e.type == "message"]
        assert any("7" in str(item.get("text") or "") for item in replies)
        # Стоп не остановил соседей: ключей у других проектов нет, kill не взводился.
        assert not any(e.type == "kill_switch" for e in runtime.bus.replay(session, 0))
        actions.release.set()
        await runtime.supervisor.wait(task_id, timeout=10)
        assert sorted(actions.started()) == _PROJECTS
    finally:
        actions.release.set()
        set_runtime(None)


_STOP_OK = {
    "останови проект 7": [7],
    "Останови проект 7.": [7],
    "стоп проект 7": [7],
    "останови проекты 7 и 8": [7, 8],
    "стоп проекты 7 и 9": [7, 9],
    "прекрати проекты 7 и 9": [7, 9],
    "останови #7, #8": [7, 8],
    "Остановите проект №7": [7],
    "Останови проект #12": [12],
}

# Фразы ревью: ни одна не должна останавливать проекты мимо планировщика.
_STOP_NOT = [
    "останови все проекты кроме проекта 5",
    "стоп всех кроме проекта 5",
    "стоп, проект 7 оставь, останови 8",
    "останови проект 7, а проект 8 не трогай",
    "останови проект 7, кроме проекта 8",
    "проект 5 не останавливай, стоп",
    "не надо стоп в проекте 5",
    "не останавливай проект 7",
    "добавь стоп-кадр в проект 7",
    "сделай стоп-кадр в проекте 7 на 3 секунде",
    "стоп-кадр в проекте 7 убери",
    "почему проект 7 стоп?",
    "останови проект 7 и удали проект 8",
    "останови проект 7 и удали его",
    "удали проект 7 и останови проект 8",
    "останови проект 7 и измени модель на kling",
    "останови проект 7 и отправь в монтаж",
    "останови проект 7 и верни как было",
    "останови проект 7 через 10 минут",
    "останови проект 7, потом запусти заново",
    "останови проект 7 и перезапусти",
    "останови проект 7 и 8 и все остальные",
    "останови проект 3 факта о котах",
    "прекрати генерацию картинок в проекте 7 и озвучь проект 8",
    "останови все проекты",
    "остановИ проект 0",
    "отмена проекта 7",
    "запусти проект 7",
    "стоп",
    "стоп 7",
]


def test_targeted_stop_parser() -> None:
    from app.orchestrator.queue import targeted_stop_projects

    for text, want in _STOP_OK.items():
        assert targeted_stop_projects(text) == want, text
    for text in _STOP_NOT:
        assert targeted_stop_projects(text) is None, text
    ten = ", ".join(str(n) for n in range(1, 11))
    assert targeted_stop_projects(f"останови проекты {ten}") == list(range(1, 11))
    # Больше десяти — обычной задачей, а не молча первые десять.
    eleven = ", ".join(str(n) for n in range(1, 12))
    assert targeted_stop_projects(f"останови проекты {eleven}") is None


@pytest.mark.asyncio
async def test_stop_reply_for_the_only_project_and_for_a_project_outside_the_task(token: str) -> None:
    del token
    actions = _Launch()
    actions.table["project.stop"] = {"risk": "low", "read_only": False, "destructive": False, "paid": False}
    runtime = _runtime(actions, [7])
    set_runtime(runtime)
    session = "par1-stop"
    try:
        await _launch_all(runtime, actions, session, [7])
        await runtime.queue.accept(session, "останови проект 7")
        await runtime.queue.drain(timeout=5)
        await runtime.queue.accept(session, "останови проект 9")
        await runtime.queue.drain(timeout=5)
        replies = [str(e.payload.get("text") or "") for e in runtime.bus.replay(session, 0)
                   if e.type == "message" and e.payload.get("kind") == "stop"]
        assert len(replies) == 2
        assert "Остальные проекты" not in replies[0]
        assert "Других проектов у задачи нет" in replies[0]
        assert "#9 не запускала" in replies[1] and "Остальные проекты" not in replies[1]
        stops = [int(c["params"]["project_id"]) for c in actions.calls if c["name"] == "project.stop"]
        assert stops == [7, 9]
    finally:
        actions.release.set()
        set_runtime(None)


@pytest.mark.asyncio
async def test_kill_key_of_a_single_step_project_is_cleared_on_resume(token: str) -> None:
    """Запуски 7, потом 8 по очереди: у задачи нет project_id, kill во время #7 ставит
    ключ одному #7. Возобновление обязано его снять, иначе #7 остаётся остановленным навсегда."""
    del token
    actions = _Launch()
    plan = [PlanStep(action="project.status", params={"project_id": p}, risk="none", parallel_group="reads")
            for p in (7, 8)]
    plan += [_run(7).model_copy(update={"parallel_group": None}),
             _run(8).model_copy(update={"parallel_group": None})]
    runtime = build_runtime(actions=actions, validators=MockValidators(), router=ScriptedRouter(),
                            planner=FixedPlanner(plan), critic=ScriptedCritic(["approve"] * 4))
    set_runtime(runtime)
    session = "par-single-kill"
    try:
        task_id = await runtime.supervisor.start_task(session, "запусти 7, потом 8")
        await runtime.supervisor.wait(task_id, timeout=5)
        await runtime.supervisor.grant_approval(session)
        await _until(lambda: actions.active == 1, "#7 in flight")
        assert actions.started() == [7]
        assert runtime.tasks.get(task_id)["project_id"] is None
        await runtime.supervisor.request_kill(session)
        assert is_stop_requested(7) and not is_stop_requested(8)
        runtime.supervisor._clear_project_stop(task_id)
        assert not is_stop_requested(7)
    finally:
        actions.release.set()
        set_runtime(None)


@pytest.mark.asyncio
async def test_kill_keys_are_cleared_by_a_fresh_supervisor_after_restart(token: str) -> None:
    del token
    actions = _Launch()
    runtime = _runtime(actions, _PROJECTS)
    set_runtime(runtime)
    session = "par6-restart"
    try:
        task_id, _ = await _launch_all(runtime, actions, session, _PROJECTS)
        await runtime.supervisor.request_kill(session)
        assert all(is_stop_requested(p) for p in _PROJECTS)
        # Человек после kill сам остановил #8 ещё раз: этот ключ его, а не задачи.
        await asyncio.sleep(0.7)
        request_stop(8, reason="user_stop")
        fresh = _runtime(_Launch(), _PROJECTS)
        set_runtime(fresh)
        fresh.supervisor._clear_project_stop(task_id)
        assert [p for p in _PROJECTS if is_stop_requested(p)] == [8]
    finally:
        actions.release.set()
        set_runtime(None)


@pytest.mark.asyncio
async def test_per_task_snapshots_are_dropped_when_the_task_ends(token: str) -> None:
    del token
    actions = _Launch()
    runtime = _runtime(actions, [5, 6])
    set_runtime(runtime)
    try:
        actions.release.set()
        task_id = await runtime.supervisor.start_task("par-forget", "запусти 5 и 6")
        await runtime.supervisor.wait(task_id, timeout=5)
        assert task_id in runtime.supervisor._planner_meta
        await runtime.supervisor.grant_approval("par-forget")
        await runtime.supervisor.wait(task_id, timeout=10)
        assert runtime.tasks.get(task_id)["status"] == TaskStatus.done.value
        assert task_id not in runtime.supervisor._planner_meta
        assert task_id not in runtime.supervisor._critic_meta
        assert task_id not in runtime.supervisor._kill_stopped
    finally:
        actions.release.set()
        set_runtime(None)
