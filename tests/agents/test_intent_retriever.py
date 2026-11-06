from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from app.orchestrator.agents import action_catalog, intent_retriever
from app.orchestrator.agents.action_catalog import ActionCatalog, boost_task, clear_boost


def _spec(action_id: str, title: str = "действие") -> SimpleNamespace:
    return SimpleNamespace(
        id=action_id,
        title_ru=title,
        description_ru=title,
        risk="read",
        spends_money=False,
        params_schema={"type": "object", "properties": {"project_id": {"type": "integer"}}},
    )


SPECS = [
    _spec("montage.queue_ops", "Очередь правок кадров"),
    _spec("montage.get_board", "Доска кадров"),
    _spec("project.status", "Состояние проекта"),
    _spec("publish.send", "Опубликовать"),
]


@pytest.fixture(autouse=True)
def _on(monkeypatch):
    monkeypatch.setenv("ORCH_INTENT_RETRIEVER", "1")


def _fake_call(monkeypatch, text: str, delay: float = 0.0, seen: list | None = None):
    async def router_call(router, tier, messages, **kwargs):
        if seen is not None:
            seen.append((tier, messages, kwargs))
        if delay:
            await asyncio.sleep(delay)
        return SimpleNamespace(text=text)

    monkeypatch.setattr("app.orchestrator.agents.llm_call.router_call", router_call)


def _run(goal: str = "кадр 3 стрёмный, давай по новой", **kw):
    return asyncio.run(
        intent_retriever.retrieve_intent(
            goal, router=object(), llm_kwargs={"task_id": 7}, specs=SPECS, project_id=10, **kw
        )
    )


def test_parses_json_and_drops_unknown_ids(monkeypatch):
    seen: list = []
    answer = {
        "intent_summary_ru": "перерисовать картинку кадра 3",
        "candidate_action_ids": ["montage.queue_ops", "made.up", "montage.get_board", "montage.queue_ops"],
        "extracted_entities": {"frame_numbers": [3, "x"], "project_ids": [], "values": {"style": "аниме"}},
        "confidence": 0.92,
        "needs_clarification": None,
    }
    _fake_call(monkeypatch, "Вот ответ: " + json.dumps(answer, ensure_ascii=False), seen=seen)
    intent = _run()
    assert intent is not None
    assert intent.candidates == ["montage.queue_ops", "montage.get_board"]
    assert intent.entities == {"frame_numbers": [3], "values": {"style": "аниме"}}
    assert intent.confidence == pytest.approx(0.92)
    assert intent.clarification is None
    tier, messages, kwargs = seen[0]
    assert tier == "fast" and kwargs["agent_role"] == "classifier"
    prompt = messages[1]["content"]
    assert "publish.send" in prompt and "проект #10" in prompt


@pytest.mark.parametrize("text", ["", "не знаю", "[1,2]", "{oops"])
def test_garbage_gives_none(monkeypatch, text):
    _fake_call(monkeypatch, text)
    assert _run() is None


def test_flag_off_skips_model(monkeypatch):
    monkeypatch.setenv("ORCH_INTENT_RETRIEVER", "0")
    seen: list = []
    _fake_call(monkeypatch, "{}", seen=seen)
    assert _run() is None
    assert seen == []


def test_timeout_gives_none(monkeypatch):
    monkeypatch.setattr(intent_retriever, "TIMEOUT_S", 0.05)
    _fake_call(monkeypatch, "{}", delay=1.0)
    assert _run() is None


def test_confidence_and_clarification_are_clamped(monkeypatch):
    _fake_call(
        monkeypatch,
        json.dumps({"candidate_action_ids": ["project.status"], "confidence": 7, "needs_clarification": " Какой? "}),
    )
    intent = _run()
    assert intent.confidence == 1.0 and intent.clarification == "Какой?"


def test_boost_adds_only_allowed_ids_and_clears(monkeypatch):
    monkeypatch.setattr(action_catalog, "select_ids", lambda text, specs, limit: ["project.status"])
    catalog = ActionCatalog(specs=lambda: SPECS, project_of=lambda _t: 10)
    assert [r["id"] for r in catalog.entries("что-то", 55)] == ["project.status"]
    boost_task(55, ["montage.queue_ops", "not.allowed"])
    try:
        ids = [r["id"] for r in catalog.entries("что-то", 55)]
        assert ids == ["montage.queue_ops", "project.status"]
        assert [r["id"] for r in catalog.entries("что-то", 56)] == ["project.status"]
    finally:
        clear_boost(55)
    assert [r["id"] for r in catalog.entries("что-то", 55)] == ["project.status"]


def test_wide_repair_doubles_lexical_limit(monkeypatch):
    limits: list[int] = []
    monkeypatch.setattr(action_catalog, "select_ids", lambda text, specs, limit: limits.append(limit) or [])
    catalog = ActionCatalog(max_catalog=24, specs=lambda: SPECS)
    boost_task(57, [], wide=True)
    try:
        catalog.entries("x", 57)
    finally:
        clear_boost(57)
    catalog.entries("x", 57)
    assert limits == [48, 24]


def test_context_note_has_summary_entities_and_candidates():
    note = intent_retriever.context_note(
        intent_retriever.Intent(
            summary="перерисовать кадр 2", candidates=["montage.queue_ops"], entities={"frame_numbers": [2]}
        )
    )
    assert "перерисовать кадр 2" in note and '"frame_numbers":[2]' in note and "montage.queue_ops" in note


def test_few_shot_only_with_flag(monkeypatch):
    from app.orchestrator.agents.planner import FEW_SHOT, _system_prompt

    assert FEW_SHOT[1] in _system_prompt([], "", "CATALOG")
    monkeypatch.setenv("ORCH_INTENT_RETRIEVER", "0")
    assert FEW_SHOT[1] not in _system_prompt([], "", "CATALOG")
    monkeypatch.setenv("ORCH_INTENT_RETRIEVER", "1")
    assert FEW_SHOT[1] not in _system_prompt([], "", "")


def test_few_shot_actions_exist_in_catalog():
    import re

    from app.orchestrator.actions import whitelist
    from app.orchestrator.agents.planner import FEW_SHOT
    from app.services.action_layer.spec import ensure_builtin_actions

    ensure_builtin_actions()
    allowed = set(whitelist())
    used = set(re.findall(r'"action":"([a-z_.]+)"', " ".join(FEW_SHOT)))
    used |= {"project.continue_pipeline", "spend.summary", "diag.incidents"}
    assert used and used <= allowed, used - allowed


@pytest.mark.parametrize(
    "goal",
    ["смени диктора на Сару", "сделай картинки для всех кадров", "кадр номер 6 какой то стремный переделай плиз"],
)
def test_wide_edit_rule_does_not_swallow_other_intents(goal):
    from app.orchestrator.agents.style_scope import wide_edit_target

    assert wide_edit_target(goal) is None


def test_wide_edit_rule_still_catches_style():
    from app.orchestrator.agents.style_scope import wide_edit_target

    assert wide_edit_target("хочу все картинки в стиле аниме") == "images"
    assert wide_edit_target("сделай сценарий короче") == "script"


@pytest.mark.parametrize("goal", ["нужно 5 видео про кошек", "надо озвучку", "хочу голос Джорджа"])
def test_wish_is_task(goal):
    from app.orchestrator.queue import classify_deterministic

    assert classify_deterministic(goal).mode.value == "task"


@pytest.mark.parametrize("goal", ["хочу узнать сколько стоит", "нужно ли перезапускать"])
def test_wish_question_is_not_forced_task(goal):
    from app.orchestrator.queue import wish_task

    assert not wish_task(goal)


def test_ordinal_frame_is_one_frame_not_all():
    from app.orchestrator.agents.style_scope import wide_edit_target

    assert wide_edit_target("в шестом кадре добавь луну на небо") is None


@pytest.mark.parametrize(
    "goal",
    ["давай уже картинки делать", "го картинки", "погнали рисовать кадры", "залей в тикток", "генери картинки",
     "дожми проект до готового видео", "сними с очереди проекты 12 и 13", "запускай видео"],
)
def test_slang_requests_are_tasks_without_model(goal):
    from app.orchestrator.queue import classify_deterministic

    found = classify_deterministic(goal)
    assert found is not None and found.mode.value == "task"


@pytest.mark.parametrize("goal", ["давай посмотрим что там", "давай статус"])
def test_lets_without_studio_work_is_not_forced_task(goal):
    from app.orchestrator.queue import classify_deterministic

    found = classify_deterministic(goal)
    assert found is None or found.mode.value != "task"


def test_number_words_become_digits_for_rules():
    from app.orchestrator.agents.frame_scope import swap_frames_plan
    from app.orchestrator.agents.number_words import words_to_digits

    assert words_to_digits("кадры три и четыре") == "кадры 3 и 4"
    plan = swap_frames_plan(words_to_digits("кадры три и четыре поменяй местами"), 10)
    assert plan and (plan[0].params["a_frame"], plan[0].params["b_frame"]) == (3, 4)


@pytest.mark.parametrize(
    "goal",
    ["можно запустить картинки?", "а можно музыку потише сделать?", "не мог бы ты перерисовать кадр 4?",
     "будь добр, выложи ролик в тикток"],
)
def test_polite_request_is_task(goal):
    from app.orchestrator.queue import classify_deterministic

    assert classify_deterministic(goal).mode.value == "task"


@pytest.mark.parametrize(
    "goal", ["можно узнать сколько стоит?", "можно ли сделать дешевле?", "можешь сказать какой голос?"]
)
def test_polite_question_stays_question(goal):
    from app.orchestrator.queue import polite_task

    assert not polite_task(goal)


def test_render_and_swap_are_not_style_edits():
    from app.orchestrator.agents.style_scope import wide_edit_target

    assert wide_edit_target("сделай рендер картинок") is None
    assert wide_edit_target("кадры 3 и 4 поменяй местами") is None


@pytest.mark.parametrize(
    "prev,reply",
    [("перерисуй третий", "и пятый тоже"), ("запусти 5 и 6 до картинок", "и 7 туда же"),
     ("сделай ролик про жирафов", "а ещё два про зебр")],
)
def test_short_continuation_of_request_becomes_task(prev, reply):
    from app.orchestrator.queue import classify_deterministic, continued_request, strip_continuation

    goal = continued_request([("assistant", "ok"), ("user", prev)], reply)
    assert goal and classify_deterministic(goal).mode.value == "task"
    assert strip_continuation(goal) == reply
    assert "5" not in goal.split(reply, 1)[1] or reply.startswith("и 7")


@pytest.mark.parametrize("prev,reply", [("какой голос", "а другие"), ("перерисуй третий", "а что с проектом")])
def test_question_follow_up_is_not_glued_to_request(prev, reply):
    from app.orchestrator.queue import continued_request

    assert continued_request([("assistant", "ok"), ("user", prev)], reply) is None


def test_explained_empty_plan_has_no_not_understood_prefix():
    from app.orchestrator.agents.summarizer import _EXPLAINS_RE

    assert _EXPLAINS_RE.search("В проекте 33 нет кадров, поэтому запуск видео невозможен")
    assert _EXPLAINS_RE.search("В проекте #10 формат кадра уже такой, менять нечего")
    assert not _EXPLAINS_RE.search("Уточните, какой кадр перерисовать")


def test_fix_and_rerun_drops_reset_of_same_step():
    from app.orchestrator.agents.run_scope import drop_needless_reset
    from app.orchestrator.contracts import PlanStep

    def st(action, **params):
        return PlanStep(action=action, params=params, expected="x")

    plan = [
        st("project.status", project_id=12),
        st("node.reset_from_step", project_id=12, step_code="img_pr"),
        st("step.run_step", project_id=12, step_code="img_pr"),
    ]
    out = drop_needless_reset(plan, "исправь и перезапусти")
    assert [s.action for s in out] == ["project.status", "step.run_step"]
    kept = drop_needless_reset(plan, "сбрось промты и запусти с нуля")
    assert len(kept) == 3
    other = [st("node.reset_from_step", project_id=12, step_code="img_pr"), st("step.run_step", project_id=12, step_code="video")]
    assert len(drop_needless_reset(other, "перезапусти")) == 2
