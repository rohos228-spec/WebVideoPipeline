"""Живой прогон фраз v8: «запусти проекты 3..12 одновременно», где один проект упирается в жёсткое
условие запуска (кадры без данных сцены). Такой проект уходит из шага до карточки, карточка
остаётся подтверждаемой для остальных и первой строкой говорит, кого и почему не поставили.
Все вызовы модели — заглушки."""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.orchestrator.agents import supervisor as sup
from app.orchestrator.agents.runtime import build_runtime, set_runtime
from app.orchestrator.contracts import PlanStep, TaskStatus
from app.services.action_layer import launch_split as ls
from app.services.action_layer.spec import Precondition
from tests.agents.mocks import FixedPlanner, MockActions, MockValidators, ScriptedCritic, ScriptedRouter

_SCENE = "кадры без данных сцены, сначала запустите ноду «Работа с GPT 2»"
_GOAL = "запусти проекты 3,4,5,6,7,8,9,10,11,12 одновременно"

_META = {
    "project.status": {"risk": "none", "read_only": True, "destructive": False, "paid": False},
    "queue.enqueue_many": {"risk": "high", "read_only": False, "destructive": False, "paid": False},
    "queue.set_parallel": {"risk": "medium", "read_only": False, "destructive": False, "paid": False},
    "step.run_step": {"risk": "high", "read_only": False, "destructive": False, "paid": True},
}


class LaunchActions(MockActions):
    """Как прод: проект из ``blocked`` не проходит условие запуска, карточка с ним — заблокирована."""

    def __init__(self, blocked: set[int]) -> None:
        super().__init__()
        self.blocked = set(blocked)
        self.probed: list[dict[str, Any]] = []

    def metadata_of(self, name: str) -> dict[str, Any]:
        if name not in _META:
            raise KeyError(name)
        return {"name": name, **_META[name]}

    def project_required(self, name: str) -> bool | None:
        return not name.startswith("queue.")

    def _ids(self, name: str, params: dict[str, Any]) -> list[int]:
        if name == "queue.enqueue_many":
            return list(params.get("project_ids") or [])
        return [params["project_id"]] if isinstance(params.get("project_id"), int) else []

    async def launch_blockers(self, name: str, params: dict[str, Any]) -> dict[str, Any] | None:
        self.probed.append({"name": name, "params": dict(params)})
        if name not in ls.SPLIT_ACTIONS:
            return None
        return {
            "blocked": [
                {"project_id": pid, "check": "img_pr_scene_input", "reason_ru": _SCENE}
                for pid in self._ids(name, params)
                if pid in self.blocked
            ]
        }

    async def prepare_card(self, name: str, params: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
        bad = [pid for pid in self._ids(name, params) if pid in self.blocked]
        if bad:
            return {"blocked": True, "preview_errors": [f"у проекта #{bad[0]} кадры без данных сцены"]}
        return {}


def _queue_plan(ids: list[int]) -> list[PlanStep]:
    return [
        PlanStep(
            action="queue.enqueue_many",
            params={"project_ids": list(ids), "enable_auto_mode": True},
            mutating=True,
            risk="high",
        )
    ]


def _run(pid: int) -> PlanStep:
    return PlanStep(
        action="step.run_step",
        params={"project_id": pid, "step_code": "img_pr", "ceiling_credits": 10},
        paid=True,
        mutating=True,
        risk="high",
        parallel_group="launch",
    )


def _status(pid: int) -> PlanStep:
    return PlanStep(action="project.status", params={"project_id": pid}, risk="none", parallel_group="reads")


def _rt(actions: LaunchActions, plan: list[PlanStep]):
    return build_runtime(
        actions=actions,
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=FixedPlanner(plan),
        critic=ScriptedCritic(["approve"] * 4),
    )


def _cards(rt: Any, session: str) -> list[dict[str, Any]]:
    return [dict(e.payload) for e in rt.bus.replay(session, 0) if e.type == "approval_required"]


def _queue_step(rt: Any, task_id: int) -> dict[str, Any]:
    saved = json.loads(rt.tasks.get(task_id)["plan_json"])
    return next(step for step in saved if step["action"] == "queue.enqueue_many")


# --- тексты ---


def test_blocked_text_names_project_reason_and_how_to_launch_later() -> None:
    text = sup._blocked_launch_text(
        "Не поставлены в очередь",
        [(10, _SCENE)],
        9,
        ("ставится в очередь как обычно", "ставятся в очередь как обычно"),
    )
    assert text.startswith(
        "Не поставлены в очередь: #10 — кадры без данных сцены, сначала запустите ноду «Работа с GPT 2»."
    ), text
    assert "Остальные 9 проектов ставятся в очередь как обычно." in text
    assert "«запусти проект 10»" in text


def test_short_reason_drops_project_number() -> None:
    assert (
        ls.short_reason("у проекта #7 выключено автопродвижение, а очередь работает только с ним")
        == "выключено автопродвижение, а очередь работает только с ним"
    )
    assert (
        ls.short_reason("проект #4 - дочерний проект фабрики, очередь генерации его пропускает")
        == "дочерний проект фабрики, очередь генерации его пропускает"
    )


def test_no_scene_short_names_the_node() -> None:
    from app.services.action_layer import img_pr_guard as ipg

    text = ipg.no_scene_short({}, [])
    assert text.startswith("кадры без данных сцены, сначала запустите ноду")


@pytest.mark.asyncio
async def test_project_blockers_checks_each_queue_project_alone() -> None:
    """Условия реестра гоняются по одному проекту: отказ #10 не роняет остальных."""
    seen: list[list[int]] = []

    async def unique(ctx: Any, params: dict[str, Any]) -> str | None:
        raise AssertionError("проверка списка не нужна по одному проекту")

    async def scene(ctx: Any, params: dict[str, Any]) -> str | None:
        seen.append(list(params["project_ids"]))
        return "у проекта #10 кадры без данных сцены" if params["project_ids"] == [10] else None

    class Spec:
        id = "queue.enqueue_many"
        preconditions = (
            Precondition("project_ids_unique", "", unique),
            Precondition("queue_ready", "", scene),
        )

    found = await ls.project_blockers(None, Spec(), {"project_ids": [3, 10, 11]})
    assert seen == [[3], [10], [11]]
    assert [item["project_id"] for item in found] == [10]
    assert found[0]["reason_ru"] == "кадры без данных сцены"


# --- карточка ---


@pytest.mark.asyncio
async def test_ten_projects_one_blocked_card_is_approvable_without_it(
    token: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    del token
    monkeypatch.setattr(sup, "_queue_limit", lambda: 10)
    actions = LaunchActions({10})
    rt = _rt(actions, _queue_plan(list(range(3, 13))))
    set_runtime(rt)
    try:
        task_id = await rt.supervisor.start_task("ph8-one", _GOAL)
        await rt.supervisor.wait(task_id, timeout=5)
        assert rt.tasks.get(task_id)["status"] == TaskStatus.waiting_approval.value
        step = _queue_step(rt, task_id)
        assert step["params"]["project_ids"] == [3, 4, 5, 6, 7, 8, 9, 11, 12]
        card = _cards(rt, "ph8-one")[-1]
        assert not card.get("blocked"), card
        warnings = card.get("warnings") or []
        assert warnings and warnings[0].startswith(f"Не поставлены в очередь: #10 — {_SCENE}."), warnings
        assert "«запусти проект 10»" in warnings[0]
        assert actions.calls == []
    finally:
        set_runtime(None)


@pytest.mark.asyncio
async def test_all_projects_blocked_card_stays_blocked(token: str, monkeypatch: pytest.MonkeyPatch) -> None:
    del token
    monkeypatch.setattr(sup, "_queue_limit", lambda: 10)
    actions = LaunchActions({3, 4, 5})
    rt = _rt(actions, _queue_plan([3, 4, 5]))
    set_runtime(rt)
    try:
        task_id = await rt.supervisor.start_task("ph8-all", "запусти проекты 3,4,5 одновременно")
        await rt.supervisor.wait(task_id, timeout=5)
        assert _queue_step(rt, task_id)["params"]["project_ids"] == [3, 4, 5]
        card = _cards(rt, "ph8-all")[-1]
        assert card.get("blocked") is True, card
        assert not any("Не поставлены" in w for w in card.get("warnings") or [])
        assert actions.calls == []
    finally:
        set_runtime(None)


@pytest.mark.asyncio
async def test_single_blocked_project_keeps_blocking(token: str) -> None:
    del token
    actions = LaunchActions({10})
    rt = _rt(actions, _queue_plan([10]))
    set_runtime(rt)
    try:
        task_id = await rt.supervisor.start_task("ph8-single", "поставь проект 10 в очередь")
        await rt.supervisor.wait(task_id, timeout=5)
        assert _queue_step(rt, task_id)["params"]["project_ids"] == [10]
        card = _cards(rt, "ph8-single")[-1]
        assert card.get("blocked") is True, card
        assert not any("Не поставлены" in w for w in card.get("warnings") or [])
        assert actions.calls == []
    finally:
        set_runtime(None)


@pytest.mark.asyncio
async def test_single_run_step_blocked_is_not_dropped(token: str) -> None:
    del token
    actions = LaunchActions({10})
    rt = _rt(actions, [_status(10), _run(10)])
    set_runtime(rt)
    try:
        task_id = await rt.supervisor.start_task("ph8-run1", "запусти промты картинок в проекте 10")
        await rt.supervisor.wait(task_id, timeout=5)
        saved = json.loads(rt.tasks.get(task_id)["plan_json"])
        assert [step["params"]["project_id"] for step in saved if step["action"] == "step.run_step"] == [10]
        card = _cards(rt, "ph8-run1")[-1]
        assert card.get("blocked") is True, card
    finally:
        set_runtime(None)


@pytest.mark.asyncio
async def test_approval_enqueues_only_remaining_projects(token: str, monkeypatch: pytest.MonkeyPatch) -> None:
    del token
    monkeypatch.setattr(sup, "_queue_limit", lambda: 10)
    actions = LaunchActions({10})
    rt = _rt(actions, _queue_plan(list(range(3, 13))))
    set_runtime(rt)
    session = "ph8-approve"
    try:
        task_id = await rt.supervisor.start_task(session, _GOAL)
        await rt.supervisor.wait(task_id, timeout=5)
        card = _cards(rt, session)[-1]
        assert not card.get("blocked")
        await rt.supervisor.grant_approval(session)
        await rt.supervisor.wait(task_id, timeout=10)
        queued = [call for call in actions.calls if call["name"] == "queue.enqueue_many"]
        assert len(queued) == 1
        assert queued[0]["params"]["project_ids"] == [3, 4, 5, 6, 7, 8, 9, 11, 12]
        assert 10 not in queued[0]["params"]["project_ids"]
        assert rt.tasks.get(task_id)["status"] == TaskStatus.done.value
    finally:
        set_runtime(None)


@pytest.mark.asyncio
async def test_mass_run_steps_drop_blocked_project(token: str) -> None:
    """Шаг на проект (не очередь): отказавший проект уходит из плана, остальные — одной карточкой."""
    del token
    actions = LaunchActions({10})
    rt = _rt(actions, [_status(9), _status(10), _status(11), _run(9), _run(10), _run(11)])
    set_runtime(rt)
    try:
        task_id = await rt.supervisor.start_task(
            "ph8-runs", "запусти промты картинок в проектах 9, 10 и 11 по шагу"
        )
        await rt.supervisor.wait(task_id, timeout=5)
        saved = json.loads(rt.tasks.get(task_id)["plan_json"])
        ids = [step["params"].get("project_id") for step in saved if step["action"] == "step.run_step"]
        queue = [step for step in saved if step["action"] == "queue.enqueue_many"]
        if queue:  # маршрут в очередь: тогда отказавший уходит из списка очереди
            assert queue[0]["params"]["project_ids"] == [9, 11]
        else:
            assert ids == [9, 11]
        card = _cards(rt, "ph8-runs")[-1]
        assert not card.get("blocked"), card
        assert (card.get("warnings") or [""])[0].startswith("Не "), card.get("warnings")
        assert "#10 — " in card["warnings"][0]
    finally:
        set_runtime(None)


def test_real_queue_spec_has_the_checks_split_relies_on() -> None:
    from app.services.action_layer.spec import ensure_builtin_actions, registry

    ensure_builtin_actions()
    ids = [pre.id for pre in registry.find("queue.enqueue_many").preconditions]
    assert "project_ids_unique" in ids and "img_pr_scene_input" in ids and "queue_ready" in ids
    run_ids = [pre.id for pre in registry.find("step.run_step").preconditions]
    assert "img_pr_scene_input" in run_ids


@pytest.mark.asyncio
async def test_other_actions_are_not_probed() -> None:
    assert await ls.launch_blockers("project.status", {"project_id": 1}) is None
