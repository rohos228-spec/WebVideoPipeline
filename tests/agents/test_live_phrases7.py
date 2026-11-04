"""Живой прогон фраз v7: запуск нескольких проектов — всегда очередь; правка настроек проекта
словами «было → станет»; пустая правка — ответ без карточки; отчёт по всем проектам без обрезки
до 10. Все вызовы модели — заглушки."""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.orchestrator.agents import supervisor as sup
from app.orchestrator.agents.launch_scope import (
    MAX_PARALLEL,
    fix_queue_target,
    launch_ask,
    route_multi_launch,
)
from app.orchestrator.agents.runtime import build_runtime, set_runtime
from app.orchestrator.contracts import PlanStep, TaskStatus
from app.services.action_layer import settings_diff as sd
from tests.agents.mocks import FixedPlanner, MockActions, MockValidators, ScriptedCritic, ScriptedRouter


def _run_step_plan(ids: list[int]) -> list[PlanStep]:
    """Как в живом прогоне v659: чтение статуса и step.run_step в каждом проекте."""
    reads = [
        PlanStep(action="project.status", params={"project_id": pid}, parallel_group="status") for pid in ids
    ]
    runs = [
        PlanStep(
            action="step.run_step",
            params={"project_id": pid, "step_code": "script"},
            paid=True,
            mutating=True,
            risk="high",
            parallel_group="launch",
        )
        for pid in ids
    ]
    return reads + runs


# --- 1. Запуск нескольких проектов — очередь генерации ---


@pytest.mark.parametrize(
    ("goal", "ids", "parallel", "target"),
    [
        ("запусти 3 4 5", [3, 4, 5], False, None),
        ("запусти проекты 3-12 одновременно", list(range(3, 13)), True, None),
        ("прогони 5,6,7 до картинок параллельно", [5, 6, 7], True, "images"),
        ("запусти проекты 3,4,5,6,7,8,9,10,11,12 одновременно", list(range(3, 13)), True, None),
        ("запусти 5 проектов параллельно до картинок: 5 6 7 8 9", [5, 6, 7, 8, 9], True, "images"),
        ("запусти с 3 по 6 до видео", [3, 4, 5, 6], False, "videos"),
        ("Прогони проекты 7, 8 до конца", [7, 8], False, None),
    ],
)
def test_multi_project_launch_is_recognized(
    goal: str, ids: list[int], parallel: bool, target: str | None
) -> None:
    ask = launch_ask(goal)
    assert ask is not None
    assert ask.project_ids == ids and ask.parallel is parallel and ask.target == target


@pytest.mark.parametrize(
    "goal",
    [
        "запусти озвучку в проектах 5 и 6",
        "запусти шаг картинок у проектов 5..9",
        "запусти следующий шаг в проектах 5 6",
        "запусти проект 5",
        "как запустить проекты 3 и 4?",
        "создай 3 проекта и запусти",
        "останови проекты 3 4 5",
    ],
)
def test_step_or_single_launch_is_not_a_queue(goal: str) -> None:
    assert launch_ask(goal) is None


@pytest.mark.parametrize(
    "goal",
    [
        "покажи статус запуска проектов 3 и 4",
        "результаты прогона проектов 5 и 6",
        "расскажи как прошёл запуск 3 и 4",
        "старт проектов 5 и 6 был удачным",
        "запусти 3 4 5 с потолком 100 рублей",
        "запусти 5 и 6, лимит 3 доллара",
        "запусти 5 и 6 за 50р",
        "запусти 2 из проектов 5,6,7",
        "запусти 5 и 6 до 3 сцены",
    ],
)
def test_reads_money_and_partial_asks_are_not_a_queue(goal: str) -> None:
    assert launch_ask(goal) is None
    plan = _run_step_plan([3, 4, 5])
    assert route_multi_launch(plan, goal, 1) == plan


def test_read_only_or_empty_plan_is_not_rewritten() -> None:
    reads = [PlanStep(action="project.status", params={"project_id": pid}) for pid in (3, 4)]
    assert route_multi_launch(reads, "запусти 3 и 4", 1) == reads
    assert route_multi_launch([], "запусти 3 и 4", 1) == []


@pytest.mark.parametrize(
    ("goal", "ids"),
    [
        ("запусти проекты 5 и 6 в 2 потока", [5, 6]),
        ("запусти 5 и 6, 9:16", [5, 6]),
        ("запусти 5 6 7 по 2 за раз", [5, 6, 7]),
    ],
)
def test_worker_and_format_numbers_are_not_projects(goal: str, ids: list[int]) -> None:
    ask = launch_ask(goal)
    assert ask is not None and ask.project_ids == ids


def test_workers_from_words_are_used() -> None:
    plan = route_multi_launch(_run_step_plan([5, 6]), "запусти проекты 5 и 6 в 2 потока", 1)
    assert [step.action for step in plan] == ["queue.set_parallel", "queue.enqueue_many"]
    assert plan[0].params == {"workers": 2}
    assert plan[1].params["project_ids"] == [5, 6]
    same = route_multi_launch(_run_step_plan([5, 6, 7]), "запусти 5 6 7 в 2 потока", 2)
    assert [step.action for step in same] == ["queue.enqueue_many"]


@pytest.mark.parametrize(
    "goal", ["запусти 3 4 5 по очереди", "запусти проект 5, потом 6", "запусти 3 4 5 друг за другом"]
)
def test_one_after_another_keeps_parallel_window(goal: str) -> None:
    ask = launch_ask(goal)
    assert ask is not None and ask.sequential and not ask.parallel
    plan = route_multi_launch(_run_step_plan(ask.project_ids), goal, 1)
    assert [step.action for step in plan] == ["queue.enqueue_many"]


def test_run_steps_become_queue_with_parallel_when_limit_is_lower() -> None:
    plan = route_multi_launch(
        _run_step_plan(list(range(3, 13))), "запусти проекты 3,4,5,6,7,8,9,10,11,12 одновременно", 2
    )
    assert [step.action for step in plan] == ["queue.set_parallel", "queue.enqueue_many"]
    assert plan[0].params == {"workers": min(10, MAX_PARALLEL)}
    assert plan[1].params == {"project_ids": list(range(3, 13)), "enable_auto_mode": True, "mode": "full"}
    # Окно не шире потолка схемы queue.set_parallel (runtime_streams.WORKER_MAX): проектов больше потолка.
    many = list(range(3, 4 + MAX_PARALLEL + 2))
    wide_goal = "запусти проекты " + ",".join(map(str, many)) + " одновременно"
    wide = route_multi_launch(_run_step_plan(many), wide_goal, 2)
    assert wide[0].action == "queue.set_parallel" and wide[0].params == {"workers": MAX_PARALLEL}


def test_member_launch_has_no_parallel_step_and_gets_plain_note() -> None:
    """Участнику окно параллели (одно на всю студию) не ставится: только очередь и строка карточки."""
    notes: list[str] = []
    plan = route_multi_launch(
        _run_step_plan(list(range(3, 13))),
        "запусти проекты 3,4,5,6,7,8,9,10,11,12 одновременно",
        2,
        lambda name: name != "queue.set_parallel",
        notes,
    )
    assert [step.action for step in plan] == ["queue.enqueue_many"]
    assert plan[0].params["project_ids"] == list(range(3, 13))
    assert len(notes) == 1 and "только админ" in notes[0]
    # Число окна — настройка всей студии: участнику его не называем (как PARALLEL_ADMIN_ONLY_MESSAGE).
    assert not any(ch.isdigit() for ch in notes[0])
    assert "$" not in notes[0] and "set_parallel" not in notes[0]
    # Окно уже на потолке: админ шире тоже не поставит — строки «меняет только админ» нет.
    capped: list[str] = []
    at_max = route_multi_launch(
        _run_step_plan(list(range(3, 13))),
        "запусти проекты 3,4,5,6,7,8,9,10,11,12 одновременно",
        MAX_PARALLEL,
        lambda name: name != "queue.set_parallel",
        capped,
    )
    assert [step.action for step in at_max] == ["queue.enqueue_many"] and capped == []
    # Окно и так вмещает все проекты: строки нет.
    quiet: list[str] = []
    fits = route_multi_launch(
        _run_step_plan([3, 4, 5]), "запусти 3 4 5", 10, lambda name: name != "queue.set_parallel", quiet
    )
    assert [step.action for step in fits] == ["queue.enqueue_many"] and quiet == []


@pytest.mark.parametrize(
    ("goal", "limit"),
    [
        ("запусти проекты 3-12 одновременно", 2),
        ("запусти проекты 3,4,5,6,7,8", 4),
        ("запусти проекты 3,4,5,6,7,8", 1),
        ("запусти 3 4 5 6 7 в 6 потоков", 2),
        ("запусти 3 4 5 6 7 в 6 потоков", None),
        ("прогони 3,4,5,6,7,8,9 до картинок параллельно", None),
    ],
)
def test_mass_launch_plan_passes_real_action_schema(goal: str, limit: int | None) -> None:
    """Окно параллели из маршрута проходит настоящую схему queue.set_parallel (не больше WORKER_MAX):
    иначе plan.py кладёт «$.workers: above N» в ошибки и карточка всего массового запуска закрыта."""
    from app.orchestrator.agents.launch_scope import MAX_PARALLEL
    from app.services.action_layer import builtin_launch
    from app.services.action_layer.spec import ensure_builtin_actions, registry
    from app.services.runtime_streams import WORKER_MAX

    assert MAX_PARALLEL == WORKER_MAX == builtin_launch.MAX_PARALLEL
    ensure_builtin_actions()
    ask = launch_ask(goal)
    assert ask is not None
    plan = route_multi_launch(_run_step_plan(ask.project_ids), goal, limit)
    assert plan and plan[-1].action == "queue.enqueue_many"
    for step in plan:
        assert registry.validate_params(step.action, step.params) == [], (step.action, step.params)
        if step.action == "queue.set_parallel":
            assert 1 <= step.params["workers"] <= WORKER_MAX


def test_window_at_cap_needs_no_parallel_step() -> None:
    from app.orchestrator.agents.launch_scope import parallel_needed

    goal = "запусти проекты 3,4,5,6,7,8,9,10,11,12 одновременно"
    plan = route_multi_launch(_run_step_plan(list(range(3, 13))), goal, MAX_PARALLEL)
    assert [step.action for step in plan] == ["queue.enqueue_many"]
    assert parallel_needed(goal, MAX_PARALLEL, 10) is False
    # Просьба шире потолка режется до потолка: окно уже там — шага нет.
    words = f"запусти 3 4 5 6 7 в {MAX_PARALLEL + 2} потоков"
    assert [s.action for s in route_multi_launch(_run_step_plan([3, 4, 5, 6, 7]), words, MAX_PARALLEL)] == [
        "queue.enqueue_many"
    ]


def test_no_parallel_step_when_limit_already_fits() -> None:
    plan = route_multi_launch(_run_step_plan([3, 4, 5]), "запусти 3 4 5 одновременно", 10)
    assert [step.action for step in plan] == ["queue.enqueue_many"]


def test_count_over_limit_sets_parallel_even_without_words() -> None:
    plan = route_multi_launch(_run_step_plan([3, 4, 5]), "запусти 3 4 5", 1)
    assert [step.action for step in plan] == ["queue.set_parallel", "queue.enqueue_many"]
    assert plan[0].params == {"workers": 3}


def test_parallel_words_with_unknown_limit_set_parallel() -> None:
    plan = route_multi_launch(_run_step_plan([5, 6, 7]), "прогони 5,6,7 до картинок параллельно", None)
    assert [step.action for step in plan] == ["queue.set_parallel", "queue.enqueue_many"]
    assert plan[1].params == {
        "project_ids": [5, 6, 7],
        "enable_auto_mode": True,
        "mode": "until_node",
        "target_node_type": "images",
    }


def test_queue_plan_from_planner_gets_valid_target_type() -> None:
    drafted = [
        PlanStep(action="queue.set_parallel", params={"workers": 5}, mutating=True, risk="medium"),
        PlanStep(
            action="queue.enqueue_many",
            params={
                "project_ids": [5, 6, 7, 8, 9],
                "enable_auto_mode": True,
                "mode": "until_node",
                "target_node_type": "img",
            },
            mutating=True,
            risk="high",
        ),
    ]
    plan = route_multi_launch(drafted, "запусти 5 проектов параллельно до картинок: 5 6 7 8 9", 3)
    assert plan[-1].params["target_node_type"] == "images"
    # Просьба не про запуск нескольких проектов: тип цели всё равно исправляется.
    assert fix_queue_target(drafted)[1].params["target_node_type"] == "images"


def test_other_plans_are_left_alone() -> None:
    plan = [
        PlanStep(action="project.patch_settings", params={"project_id": 3, "image_generator": "flux_2_pro"}),
        *_run_step_plan([3, 4]),
    ]
    assert route_multi_launch(plan, "поменяй модель и запусти 3 4", 5) == plan
    single = _run_step_plan([5])
    assert route_multi_launch(single, "запусти шаг озвучки в проектах 5 и 6", 5) == single


_META = {
    "project.status": {"risk": "none", "read_only": True, "destructive": False, "paid": False},
    "queue.enqueue_many": {"risk": "high", "read_only": False, "destructive": False, "paid": False},
    "queue.set_parallel": {"risk": "medium", "read_only": False, "destructive": False, "paid": False},
    "step.run_step": {"risk": "high", "read_only": False, "destructive": False, "paid": True},
}


class MetaActions(MockActions):
    def __init__(self, meta: dict[str, dict[str, Any]]) -> None:
        super().__init__()
        self.meta = meta

    def metadata_of(self, name: str) -> dict[str, Any]:
        if name not in self.meta:
            raise KeyError(name)
        return {"name": name, **self.meta[name]}


@pytest.mark.asyncio
async def test_supervisor_turns_ten_run_steps_into_queue_card(
    token: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    del token
    monkeypatch.setattr(sup, "_queue_limit", lambda: 2)
    actions = MetaActions(_META)
    rt = build_runtime(
        actions=actions,
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=FixedPlanner(_run_step_plan(list(range(3, 13)))),
        critic=ScriptedCritic([]),
    )
    set_runtime(rt)
    try:
        task_id = await rt.supervisor.start_task(
            "ph7-queue", "запусти проекты 3,4,5,6,7,8,9,10,11,12 одновременно"
        )
        await rt.supervisor.wait(task_id, timeout=5)
        saved = json.loads(rt.tasks.get(task_id)["plan_json"])
        assert [step["action"] for step in saved] == ["queue.set_parallel", "queue.enqueue_many"]
        assert saved[0]["params"] == {"workers": min(10, MAX_PARALLEL)}
        assert saved[1]["params"]["project_ids"] == list(range(3, 13))
        assert rt.tasks.get(task_id)["status"] == TaskStatus.waiting_approval.value
        assert [c["name"] for c in actions.calls] == []
    finally:
        set_runtime(None)


# --- 2-3. Правка настроек проекта словами; пустая правка — ответ без карточки ---


def test_change_text_uses_human_labels_and_default() -> None:
    items = sd.diff({"image_generator": "flux_2_pro"}, {"project_id": 1, "image_generator": "nano_banana_2"})
    text = sd.change_text(items, as_default=True)
    assert text == "модель картинок: Flux 2 Pro → по умолчанию (Nano Banana 2 (1K/2K/4K))"
    assert "image_generator" not in text


def test_empty_project_field_counts_as_default_only_when_default_is_asked() -> None:
    items = sd.diff({"image_generator": None}, {"project_id": 1, "image_generator": "nano_banana_2"})
    # «Поставь модель по умолчанию»: пустое поле — уже по умолчанию.
    assert items[0]["same_default"] is True
    assert sd.same_text(items, True) == "модель картинок уже по умолчанию (Nano Banana 2 (1K/2K/4K))"
    # «Закрепи Nano Banana 2 (1K/2K/4K)»: пустое поле — не то же самое, это правка.
    assert items[0]["same"] is False
    assert (
        sd.change_text(items)
        == "модель картинок: по умолчанию (Nano Banana 2 (1K/2K/4K)) → Nano Banana 2 (1K/2K/4K)"
    )
    assert sd.summary(items)["unchanged"] is False and sd.summary(items)["unchanged_default"] is True


def test_explicit_model_that_is_default_is_not_called_default() -> None:
    items = sd.diff({"image_generator": "flux_2_pro"}, {"project_id": 1, "image_generator": "nano_banana_2"})
    assert sd.change_text(items) == "модель картинок: Flux 2 Pro → Nano Banana 2 (1K/2K/4K)"


def test_settings_effects_have_no_raw_keys() -> None:
    from app.services.action_layer.builtin_wave5 import _settings_effects

    text = _settings_effects({"project_id": 5, "image_generator": "nano_banana_2", "image_resolution": "2k"})
    assert "image_generator" not in text and "image_resolution" not in text
    assert "модель картинок → Nano Banana 2 (1K/2K/4K)" in text and "разрешение картинок → 2K" in text


class DiffActions(MockActions):
    """Проекты: #5 и #6 уже с Nano Banana 2 (1K/2K/4K), #7 — Flux 2 Pro."""

    current = {5: "nano_banana_2", 6: "nano_banana_2", 7: "flux_2_pro", 8: None}

    async def settings_diff(self, name: str, params: dict[str, Any]) -> dict[str, Any] | None:
        return sd.summary(sd.diff({"image_generator": self.current.get(params["project_id"])}, params))


def _patch(pid: int) -> PlanStep:
    return PlanStep(
        action="project.patch_settings_costly",
        params={"project_id": pid, "image_generator": "nano_banana_2"},
        mutating=True,
        risk="high",
    )


@pytest.mark.asyncio
async def test_noop_patch_answers_without_card(token: str) -> None:
    del token
    rt = build_runtime(
        actions=DiffActions(risks={"project.patch_settings_costly": "high"}),
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=FixedPlanner([_patch(5)]),
        critic=ScriptedCritic([]),
    )
    set_runtime(rt)
    try:
        task_id = await rt.supervisor.start_task("ph7-noop", "смени модель картинок на gpt в проекте 5")
        await rt.supervisor.wait(task_id, timeout=5)
        events = list(rt.bus.replay("ph7-noop", 0))
        assert not [e for e in events if e.type == "approval_required"]
        texts = " ".join(str(e.payload.get("text") or "") for e in events if e.type == "message")
        assert "В проекте #5 модель картинок уже Nano Banana 2 (1K/2K/4K), менять нечего." in texts, texts
        assert "не понял" not in texts.lower()
    finally:
        set_runtime(None)


@pytest.mark.asyncio
async def test_mass_patch_skips_unchanged_projects_and_says_what_changes(token: str) -> None:
    del token
    rt = build_runtime(
        actions=DiffActions(risks={"project.patch_settings_costly": "high"}),
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=FixedPlanner([_patch(5), _patch(6), _patch(7)]),
        critic=ScriptedCritic(["approve"] * 3),
    )
    set_runtime(rt)
    try:
        task_id = await rt.supervisor.start_task(
            "ph7-mass", "во всех проектах поменяй модель картинок на дефолтную"
        )
        await rt.supervisor.wait(task_id, timeout=5)
        saved = json.loads(rt.tasks.get(task_id)["plan_json"])
        assert [step["params"]["project_id"] for step in saved] == [7]
        cards = [e.payload for e in rt.bus.replay("ph7-mass", 0) if e.type == "approval_required"]
        assert cards
        card = cards[-1]
        lines = " ".join(str(view.get("text") or "") for view in card.get("steps") or [])
        assert "модель картинок: Flux 2 Pro → по умолчанию (Nano Banana 2 (1K/2K/4K))" in lines, lines
        assert "image_generator" not in lines
        assert any("Пропущено 2 проекта (#5, #6)" in w for w in card.get("warnings") or []), card.get(
            "warnings"
        )
    finally:
        set_runtime(None)


@pytest.mark.asyncio
async def test_mass_patch_where_nothing_changes_answers(token: str) -> None:
    del token
    rt = build_runtime(
        actions=DiffActions(risks={"project.patch_settings_costly": "high"}),
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=FixedPlanner([_patch(5), _patch(6)]),
        critic=ScriptedCritic([]),
    )
    set_runtime(rt)
    try:
        task_id = await rt.supervisor.start_task(
            "ph7-none", "во всех проектах поменяй модель картинок на дефолтную"
        )
        await rt.supervisor.wait(task_id, timeout=5)
        events = list(rt.bus.replay("ph7-none", 0))
        assert not [e for e in events if e.type == "approval_required"]
        texts = " ".join(str(e.payload.get("text") or "") for e in events if e.type == "message")
        assert (
            "Во всех 2 проектах (#5, #6) модель картинок уже Nano Banana 2 (1K/2K/4K), менять нечего."
            in texts
        ), texts
    finally:
        set_runtime(None)


@pytest.mark.asyncio
async def test_single_patch_card_says_what_changes(token: str) -> None:
    del token
    rt = build_runtime(
        actions=DiffActions(risks={"project.patch_settings_costly": "high"}),
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=FixedPlanner([_patch(7)]),
        critic=ScriptedCritic(["approve"] * 3),
    )
    set_runtime(rt)
    try:
        task_id = await rt.supervisor.start_task("ph7-one", "смени модель картинок на gpt в проекте 7")
        await rt.supervisor.wait(task_id, timeout=5)
        cards = [e.payload for e in rt.bus.replay("ph7-one", 0) if e.type == "approval_required"]
        assert cards
        lines = " ".join(str(view.get("text") or "") for view in cards[-1].get("steps") or [])
        assert "модель картинок: Flux 2 Pro → Nano Banana 2 (1K/2K/4K); проект #7" in lines, lines
        assert "по умолчанию" not in lines
    finally:
        set_runtime(None)


@pytest.mark.asyncio
async def test_pinning_model_on_project_without_own_model_is_a_change(token: str) -> None:
    del token
    rt = build_runtime(
        actions=DiffActions(risks={"project.patch_settings_costly": "high"}),
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=FixedPlanner([_patch(8)]),
        critic=ScriptedCritic(["approve"] * 3),
    )
    set_runtime(rt)
    try:
        task_id = await rt.supervisor.start_task("ph7-pin", "закрепи модель картинок gpt в проекте 8")
        await rt.supervisor.wait(task_id, timeout=5)
        cards = [e.payload for e in rt.bus.replay("ph7-pin", 0) if e.type == "approval_required"]
        assert cards
        lines = " ".join(str(view.get("text") or "") for view in cards[-1].get("steps") or [])
        assert (
            "модель картинок: по умолчанию (Nano Banana 2 (1K/2K/4K)) → Nano Banana 2 (1K/2K/4K); проект #8"
            in lines
        ), lines
    finally:
        set_runtime(None)


@pytest.mark.asyncio
async def test_default_ask_on_project_without_own_model_answers(token: str) -> None:
    del token
    rt = build_runtime(
        actions=DiffActions(risks={"project.patch_settings_costly": "high"}),
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=FixedPlanner([_patch(8)]),
        critic=ScriptedCritic([]),
    )
    set_runtime(rt)
    try:
        task_id = await rt.supervisor.start_task(
            "ph7-def", "поставь модель картинок по умолчанию в проекте 8"
        )
        await rt.supervisor.wait(task_id, timeout=5)
        events = list(rt.bus.replay("ph7-def", 0))
        assert not [e for e in events if e.type == "approval_required"]
        texts = " ".join(str(e.payload.get("text") or "") for e in events if e.type == "message")
        assert (
            "В проекте #8 модель картинок уже по умолчанию (Nano Banana 2 (1K/2K/4K)), менять нечего."
            in texts
        ), texts
    finally:
        set_runtime(None)
