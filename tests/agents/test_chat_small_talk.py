"""Вежливые реплики, «помоги», стоп и откат: чат всегда отвечает и не рисует ошибку."""

from __future__ import annotations

import pytest

pytest.importorskip("app.orchestrator.actions")

from app.orchestrator.agents.runtime import build_runtime, set_runtime  # noqa: E402
from app.orchestrator.agents.planner import unreadable_plan  # noqa: E402
from app.orchestrator.agents.summarizer import (  # noqa: E402
    WAITING_OTHER,
    TaskFacts,
    deterministic_text,
    raw_reason,
)
from app.orchestrator.contracts import MessageMode, PlanStep, TaskStatus  # noqa: E402
from app.orchestrator.queue import STEER_IDLE_TEXT, classify_deterministic, stop_reply  # noqa: E402
from app.orchestrator.routing.classifier import classify_rules  # noqa: E402
from app.orchestrator.small_talk import (  # noqa: E402
    CARD_WAITING_TEXT,
    HELP_EXAMPLES,
    HELP_TEXT,
    OK_TEXT,
    small_talk_reply,
)
from tests.agents.mocks import FixedPlanner, MockActions, MockValidators, ScriptedRouter  # noqa: E402


class _CountingPlanner(FixedPlanner):
    def __init__(self) -> None:
        super().__init__([])
        self.calls = 0

    async def make_plan(self, goal, context="", **kwargs):
        self.calls += 1
        return await super().make_plan(goal, context, **kwargs)


def _events(rt, session: str, kind: str) -> list:
    return [event for event in rt.bus.replay(session, 0) if event.type == kind]


def _runtime(router: ScriptedRouter, planner=None, actions=None):
    rt = build_runtime(actions=actions or MockActions(), validators=MockValidators(), router=router,
                       planner=planner or _CountingPlanner())
    set_runtime(rt)
    return rt


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["привет", "ок", "спасибо", "?", "помоги", "что ты умеешь?", "хз что делать"])
async def test_small_talk_answers_without_a_task(token: str, text: str) -> None:
    del token
    router = ScriptedRouter()
    router.fail_if_called = True
    planner = _CountingPlanner()
    rt = _runtime(router, planner)
    session = "talk-" + str(abs(hash(text)))
    try:
        await rt.queue.accept(session, text)
        await rt.queue.drain(timeout=5)
        messages = [event.payload for event in _events(rt, session, "message")]
        assert len(messages) == 1 and messages[0]["kind"] == "small_talk"
        assert messages[0]["text"] == small_talk_reply(text)
        assert "Не получилось" not in messages[0]["text"]
        # Ни задачи, ни статуса, ни планировщика: значок не станет красным.
        assert _events(rt, session, "agent_status") == []
        assert _events(rt, session, "task_failed") == []
        assert planner.calls == 0 and router.call_count == 0 and router.classify_calls == 0
        assert _events(rt, session, "classification")[-1].payload["mode"] == "append"
    finally:
        set_runtime(None)


def test_help_lists_examples_the_chat_really_handles() -> None:
    for phrase in ("помоги", "что ты умеешь", "хз что делать", "?"):
        assert small_talk_reply(phrase) == HELP_TEXT
    assert 4 <= len(HELP_EXAMPLES) <= 6
    for phrase in HELP_EXAMPLES:
        assert f"«{phrase}»" in HELP_TEXT
        # Пример — настоящая просьба: не вежливость и не стоп, а вопрос или задача.
        assert small_talk_reply(phrase) is None
        forced = classify_deterministic(phrase)
        mode = forced.mode.value if forced is not None else classify_rules(phrase).mode
        assert mode in {"append", "task"}, phrase


def test_requests_are_not_small_talk() -> None:
    for text in ("помоги запустить проект 6", "да", "что с проектом 4", "останови все", "пожалуйста"):
        assert small_talk_reply(text) is None, text


@pytest.mark.asyncio
async def test_stop_without_a_task_still_answers(token: str) -> None:
    del token
    rt = _runtime(ScriptedRouter())
    session = "talk-stop-idle"
    try:
        await rt.queue.accept(session, "останови все")
        await rt.queue.drain(timeout=5)
        assert _events(rt, session, "classification")[-1].payload["mode"] == "interrupt"
        messages = [event.payload for event in _events(rt, session, "message")]
        assert [item["kind"] for item in messages] == ["stop"]
        assert messages[0]["text"].startswith("Сейчас нечего останавливать")
        assert "останови проект 5" in messages[0]["text"]
    finally:
        set_runtime(None)


def test_stop_reply_says_what_was_stopped() -> None:
    running = stop_reply("сделай отчёт", "executing", True)
    assert running.startswith("Останавливаю задачу чата «сделай отчёт»")
    assert "«Стоп задачи»" in running and "Генерацию в проектах это не останавливает" in running
    # В большом окне чата та же кнопка называется иначе.
    assert "«Остановить задачу»" in running
    waiting = stop_reply("запусти картинки", "waiting_approval", True)
    assert "«Отклонить»" in waiting and "Останавливаю" not in waiting


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["отмени последнее действие", "отмени последнюю правку", "верни как было", "откати"])
async def test_undo_phrases_reach_planning_not_interrupt(token: str, text: str) -> None:
    del token
    router = ScriptedRouter()
    # Настоящий классификатор отдал бы «отмени …» в прерывание по стоп-слову.
    router.mode = MessageMode.interrupt
    planner = _CountingPlanner()
    rt = _runtime(router, planner)
    session = "talk-undo-" + str(abs(hash(text)))
    try:
        await rt.queue.accept(session, text)
        await rt.queue.drain(timeout=5)
        assert _events(rt, session, "classification")[-1].payload["mode"] == "task"
        task_id = _events(rt, session, "agent_status")[0].payload["task_id"]
        await rt.supervisor.wait(task_id, timeout=5)
        assert planner.calls >= 1
    finally:
        set_runtime(None)


def test_router_rule_alone_would_swallow_undo() -> None:
    assert classify_rules("отмени последнее действие").mode == "interrupt"
    assert classify_deterministic("отмени последнее действие").mode is MessageMode.task
    assert classify_deterministic("стоп").mode is MessageMode.interrupt


@pytest.mark.parametrize("text", [
    "Уточните, что именно вернуть: проект, ноду или промт, и укажите project_id и node_key.",
    "GPT 5.6 Sol дороже, а действие node.set_model_costly отсутствует в каталоге.",
    "В каталоге нет действия для удаления проекта 2.",
    "Нет подходящего action.",
])
def test_raw_planner_reason_is_never_shown(text: str) -> None:
    assert raw_reason(text)
    facts = TaskFacts(goal="верни как было", status=TaskStatus.cancelled.value, error="empty_plan", detail=text)
    shown = deterministic_text(facts)
    assert shown.startswith("Не понял, что сделать.") and text not in shown
    for word in ("project_id", "node", "каталог", "action", "Не получилось"):
        assert word not in shown


def test_plain_planner_question_is_kept() -> None:
    why = "В каком проекте сменить модель на GPT 5.6 Sol?"
    assert not raw_reason(why)
    shown = deterministic_text(TaskFacts(goal="смени модель", status="cancelled", error="empty_plan", detail=why))
    assert shown.splitlines() == [
        "Не понял, что сделать.",
        why,
        "Напишите номер проекта и что нужно, например «запусти картинки в проекте 6» или «что с проектом 4». "
        "Если не знаете, с чего начать, напишите «помоги».",
    ]


def test_rejection_is_not_a_failure_text() -> None:
    text = deterministic_text(TaskFacts(goal="видео", status="cancelled", error="rejected"))
    assert text == "Отменено по вашему решению. Ничего не изменено."


@pytest.mark.parametrize("text", [
    "стоп, отмени последнее", "стоп отмени последнее", "остановись и отмени последнее", "стоп undo",
    "отмени последнюю задачу", "отмена последнего запуска",
])
def test_explicit_stop_beats_undo(text: str) -> None:
    # Иначе «стоп …» уходит в план отката, а идущая задача не останавливается.
    assert classify_deterministic(text).mode is MessageMode.interrupt, text


@pytest.mark.asyncio
async def test_stop_with_undo_cancels_running_task(token: str) -> None:
    del token
    actions = MockActions()
    planner = FixedPlanner([PlanStep(action="mock_sleep", params={"_block": True, "_seconds": 30},
                                     expected="wait", paid=False, mutating=False)])
    rt = _runtime(ScriptedRouter(), planner, actions)
    session = "talk-stop-undo"
    try:
        task_id = await rt.supervisor.start_task(session, "долгий шаг")
        await actions.started.wait()
        await rt.queue.accept(session, "стоп, отмени последнее")
        await rt.supervisor.wait(task_id, timeout=8)
        await rt.queue.drain(timeout=5)
        assert rt.tasks.get(task_id)["status"] == TaskStatus.cancelled.value
        assert [event.payload.get("kind") for event in _events(rt, session, "message")].count("stop") == 1
    finally:
        set_runtime(None)


@pytest.mark.asyncio
@pytest.mark.parametrize(("text", "kind"), [("учти что проект 5 вертикальный", "steer_idle"),
                                            ("аварийный стоп", "stop")])
async def test_steer_and_kill_without_a_task_still_answer(token: str, text: str, kind: str) -> None:
    del token
    rt = _runtime(ScriptedRouter())
    session = "talk-idle-" + kind
    try:
        await rt.queue.accept(session, text)
        await rt.queue.drain(timeout=5)
        messages = [event.payload for event in _events(rt, session, "message")]
        # Без ответа значок чата навсегда остаётся «Думаю…».
        assert [item["kind"] for item in messages] == [kind]
        assert messages[0]["text"] == (STEER_IDLE_TEXT if kind == "steer_idle" else stop_reply("", "", False))
    finally:
        set_runtime(None)


def _paid_step() -> PlanStep:
    return PlanStep(action="paid_mock", params={"_wait": True, "_change_id": "chg-paid", "_cost": 1},
                    expected="paid", paid=True, mutating=True)


@pytest.mark.asyncio
async def test_stop_on_unknown_paid_outcome_does_not_point_to_a_gone_card(token: str) -> None:
    del token
    actions = MockActions()
    rt = _runtime(ScriptedRouter(), FixedPlanner([_paid_step()]), actions)
    session = "talk-stop-paid"
    try:
        task_id = await rt.supervisor.start_task(session, "платный", project_id=13)
        await rt.supervisor.wait(task_id, timeout=5)
        await rt.supervisor.grant_approval(session)
        await actions.started.wait()
        await rt.queue.accept(session, "стой")
        await rt.queue.drain()
        actions.release.set()
        await rt.supervisor.wait(task_id, timeout=5)
        assert rt.supervisor.stop_closes_card(task_id)
        await rt.queue.accept(session, "стоп")
        await rt.queue.drain(timeout=5)
        stops = [event.payload["text"] for event in _events(rt, session, "message")
                 if event.payload.get("kind") == "stop"]
        assert stops[-1].startswith("Останавливаю задачу чата") and "«Отклонить»" not in stops[-1]
    finally:
        set_runtime(None)


@pytest.mark.asyncio
async def test_ok_while_card_waits_reminds_about_buttons(token: str) -> None:
    del token
    rt = _runtime(ScriptedRouter(), FixedPlanner([_paid_step()]))
    session = "talk-ok-card"
    try:
        task_id = await rt.supervisor.start_task(session, "платный", project_id=13)
        await rt.supervisor.wait(task_id, timeout=5)
        assert rt.tasks.get(task_id)["status"] == TaskStatus.waiting_approval.value
        for text in ("ок", "\U0001F44D"):
            await rt.queue.accept(session, text)
            await rt.queue.drain(timeout=5)
            assert _events(rt, session, "message")[-1].payload["text"] == CARD_WAITING_TEXT
        # «ок» ничего не подтвердил: карточка по-прежнему ждёт.
        assert rt.tasks.get(task_id)["status"] == TaskStatus.waiting_approval.value
    finally:
        set_runtime(None)


def test_emoji_is_agreement_not_a_help_request() -> None:
    assert small_talk_reply("\U0001F44D") == OK_TEXT
    assert small_talk_reply("?") == HELP_TEXT and small_talk_reply("...") == HELP_TEXT


class _BrokenPlanner(_CountingPlanner):
    async def make_plan(self, goal, context="", **kwargs):
        self.last_parse_failed = True
        return await super().make_plan(goal, context, **kwargs)


@pytest.mark.asyncio
async def test_unreadable_planner_answer_is_a_failure_not_not_understood(token: str) -> None:
    del token
    rt = _runtime(ScriptedRouter(), _BrokenPlanner())
    session = "talk-broken-plan"
    try:
        task_id = await rt.supervisor.start_task(session, "запусти картинки в проекте 6")
        await rt.supervisor.wait(task_id, timeout=5)
        assert rt.tasks.get(task_id)["status"] == TaskStatus.failed.value
        text = [event.payload["text"] for event in _events(rt, session, "message")][-1]
        assert "Не понял" not in text and "планировщик не ответил" in text
    finally:
        set_runtime(None)


def test_planner_flags_empty_or_broken_json() -> None:
    assert unreadable_plan("") and unreadable_plan('{"steps": [') and unreadable_plan("не знаю")
    assert not unreadable_plan('{"steps": [], "reason": "В каком проекте?"}')


def test_waiting_card_note_survives_raw_reason() -> None:
    note = WAITING_OTHER.format(goal="модель kling_v2.1 в json")
    detail = "Нужна модель kling_v2.1. " + note
    shown = deterministic_text(TaskFacts(goal="x", status="cancelled", error="empty_plan", detail=detail))
    assert note in shown and "Нужна модель" not in shown


def test_asked_approval_reason_gives_one_instruction() -> None:
    from app.orchestrator.agents.supervisor import _ASKED_APPROVAL

    shown = deterministic_text(TaskFacts(goal="x", status="cancelled", error="empty_plan", detail=_ASKED_APPROVAL))
    assert "Причина:" not in shown and shown.count("например") == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["проект 12 на каком шаге стоит?", "сколько стоит шаг видео в проекте 4?"])
async def test_model_interrupt_without_a_stop_word_is_a_question(token: str, text: str) -> None:
    del token
    router = ScriptedRouter()
    # Модель-классификатор читала «стоит» как «стоп».
    router.mode = MessageMode.interrupt
    rt = _runtime(router)
    session = "talk-stoit-" + str(abs(hash(text)))
    try:
        await rt.queue.accept(session, text)
        await rt.queue.drain(timeout=5)
        assert _events(rt, session, "classification")[-1].payload["mode"] == "append"
    finally:
        set_runtime(None)


def test_name_it_with_a_quoted_title_is_a_rename_task() -> None:
    assert classify_deterministic("назови проект 12 «Муравьи тест»").mode is MessageMode.task
    assert classify_deterministic("назови модели картинок") is None or (
        classify_deterministic("назови модели картинок").mode is not MessageMode.task
    )
