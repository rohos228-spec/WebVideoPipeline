"""Живой прогон фраз v6 (201-227): быстрая карточка очереди, все проекты на карточке, предел
параллельных в фактах вопроса, правка только названной настройки, «спасибо, все», ссылки с
пробелом, текст промта ноды, «модель на gpt» — вопрос. Все вызовы модели — заглушки."""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.orchestrator.agents import supervisor as sup
from app.orchestrator.agents.planner import RouterPlanner
from app.orchestrator.agents.question_context import QUEUE_TITLE, QuestionContext
from app.orchestrator.agents.runtime import build_runtime, set_runtime
from app.orchestrator.agents.settings_scope import named_keys, scope_settings
from app.orchestrator.agents.summarizer import (
    StepNote,
    TaskFacts,
    TaskSummarizer,
    gpt_model_clarify_text,
    normalize_links,
)
from app.orchestrator.contracts import PlanStep, TaskStatus
from app.orchestrator.small_talk import CARD_WAITING_TEXT, THANKS_TEXT, is_decline, small_talk_reply
from app.services.action_layer.planner_rules import param_parts_ru
from tests.agents.mocks import FixedPlanner, MockActions, MockValidators, ScriptedCritic, ScriptedRouter

_RUN = "orch-task-1"
READ_META = {"risk": "none", "read_only": True, "destructive": False, "paid": False}
QUEUE_META = {"risk": "high", "read_only": False, "destructive": False, "paid": False}
PARALLEL_META = {"risk": "medium", "read_only": False, "destructive": False, "paid": False}


class MetaActions(MockActions):
    def __init__(self, meta: dict[str, dict[str, Any]]) -> None:
        super().__init__()
        self.meta = meta

    def metadata_of(self, name: str) -> dict[str, Any]:
        if name not in self.meta:
            raise KeyError(name)
        return {"name": name, **self.meta[name]}


def _queue_plan(ids: list[int]) -> list[PlanStep]:
    reads = [PlanStep(action="project.status", params={"project_id": pid}) for pid in ids]
    return reads + [
        PlanStep(action="queue.set_parallel", params={"workers": len(ids)}, mutating=True, risk="medium"),
        PlanStep(action="queue.enqueue_many", params={"project_ids": ids, "enable_auto_mode": True,
                                                      "mode": "until_node", "target_node_type": "images"},
                 mutating=True, risk="high"),
    ]


_META = {"project.status": READ_META, "queue.enqueue_many": QUEUE_META, "queue.set_parallel": PARALLEL_META,
         "step.run_step": {"risk": "high", "read_only": False, "destructive": False, "paid": True}}


# --- #207: карточка запуска нескольких проектов без круга «критик → планировщик» ---

@pytest.mark.asyncio
async def test_multi_project_launch_card_skips_critic(token: str) -> None:
    """#207: критик просил «сначала прочитай статус», план собирался заново — карточка через 2+ минуты."""
    del token
    actions = MetaActions(_META)
    critic = ScriptedCritic([])  # вызов критика упал бы: список вердиктов пуст
    rt = build_runtime(actions=actions, validators=MockValidators(), router=ScriptedRouter(),
                       planner=FixedPlanner(_queue_plan([5, 6, 7, 8, 9])), critic=critic)
    set_runtime(rt)
    try:
        task_id = await rt.supervisor.start_task("ph6-queue", "запусти 5 проектов параллельно до картинок: 5 6 7 8 9")
        await rt.supervisor.wait(task_id, timeout=5)
        assert rt.tasks.get(task_id)["status"] == TaskStatus.waiting_approval.value
        cards = [e for e in rt.bus.replay("ph6-queue", 0) if e.type == "approval_required"]
        assert cards
        assert [c["name"] for c in actions.calls if c["name"].startswith("queue.")] == []
    finally:
        set_runtime(None)


def test_queue_launch_plan_only_queue_and_free_reads() -> None:
    actions = MetaActions(_META)
    assert sup._queue_launch_plan(_queue_plan([3, 4]), actions)
    paid = PlanStep(action="step.run_step", params={"project_id": 3, "step_code": "img"}, paid=True,
                    mutating=True, risk="high")
    assert not sup._queue_launch_plan(_queue_plan([3]) + [paid], actions)
    assert not sup._queue_launch_plan([PlanStep(action="project.status", params={"project_id": 3})], actions)
    # Шаг очереди без карточки (риск none и в реестре, и в плане) — критик остаётся.
    low = MetaActions({**_META, "queue.enqueue_many": {**QUEUE_META, "risk": "none"},
                       "queue.set_parallel": {**PARALLEL_META, "risk": "none"}})
    quiet = [step.model_copy(update={"risk": "none"}) for step in _queue_plan([3])]
    assert not sup._queue_launch_plan(quiet, low)


# --- #208: на карточке все названные проекты ---

def test_card_lists_all_ten_projects() -> None:
    """#208: в шаге были 3..12, карточка показывала «проекты 3, 4, 5, 6, 7, 8»."""
    ids = [3, 4, 5, 6, 7, 8, 9, 10, 11, 12]
    parts = param_parts_ru({"project_ids": ids, "enable_auto_mode": True, "mode": "full"})
    assert parts[0] == "проекты 3, 4, 5, 6, 7, 8, 9, 10, 11, 12"


def test_card_compresses_long_project_lists_and_says_how_many_left() -> None:
    parts = param_parts_ru({"project_ids": list(range(1, 41))})
    assert parts[0] == "проекты 1–40"
    many = list(range(1, 120, 2))
    text = param_parts_ru({"project_ids": many})[0]
    assert text.startswith("проекты 1, 3, 5")
    shown = text.removeprefix("проекты ").split(" и ещё ")[0].split(", ")
    assert text.endswith(f" и ещё {len(many) - len(shown)}")


# --- #209: предел параллельных в фактах вопроса ---

@pytest.mark.asyncio
async def test_question_about_parallel_limit_has_the_number() -> None:
    """#209: «Сколько максимум может идти, в данных не указано» — число известно студии."""
    seen: list[str] = []

    async def runner(action: str, params: dict[str, Any], app: Any) -> Any:
        seen.append(action)
        return {"text_ru": "Проектов: 14; показано 14, идут сейчас 0."}

    context = QuestionContext(runner=runner, queue_of=lambda: "Сколько проектов очереди генерации может идти "
                                                              "одновременно (настройка студии): 10.")
    facts = await context.gather("сколько проектов может идти одновременно", None)
    queue = [fact for fact in facts if fact.title == QUEUE_TITLE]
    assert queue and "одновременно (настройка студии): 10" in queue[0].text
    plain = await context.gather("что с проектом 5", 5)
    assert not any(fact.title == QUEUE_TITLE for fact in plain)


def test_queue_capacity_text_reads_worker_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.orchestrator.agents import question_context as qc

    monkeypatch.setattr("app.services.gen_queue.worker_max_parallel", lambda: 7)
    monkeypatch.setattr("app.services.sidebar_layout.get_gen_queue", lambda: [5, 6])
    # Файл очереди общий для студии: в ответ идут только свои проекты (на этой линии — по арендатору).
    monkeypatch.setattr(
        "app.services.orch_tenancy.own_projects", lambda _tenant, ids: [(pid, "") for pid in ids]
    )
    text = qc.queue_capacity_text()
    assert "одновременно (настройка студии): 7" in text
    assert "Ваших проектов в очереди сейчас: 2 (#5, #6)." in text
    monkeypatch.setattr(
        "app.services.orch_tenancy.own_projects", lambda _tenant, ids: [(pid, "") for pid in ids if pid == 5]
    )
    text = qc.queue_capacity_text()
    assert "Ваших проектов в очереди сейчас: 1 (#5)." in text and "#6" not in text


# --- #213: правка только названной настройки ---

_FULL = {"aspect_ratio": "9:16", "hero_mode": "auto", "image_generator": "gpt_image_2_vip",
         "image_resolution": "1k", "title": "2 факта о лисах", "topic": "2 факта о лисах",
         "video_generator": "veo_3_1_lite", "video_resolution": "720p"}


def test_mass_image_model_patch_keeps_only_image_generator() -> None:
    """#213: в каждом проекте менялись название, тема, формат, герой, разрешение и модель видео."""
    goal = "во всех проектах поменяй модель картинок на дефолтную"
    plan = [PlanStep(action="project.patch_settings_costly", params={"project_id": pid, **_FULL},
                     mutating=True, risk="high") for pid in (1, 2, 3)]
    scoped = scope_settings(plan, goal)
    assert [step.params for step in scoped] == [{"project_id": pid, "image_generator": "gpt_image_2_vip"}
                                                for pid in (1, 2, 3)]


@pytest.mark.parametrize(("goal", "params", "kept"), [
    ("поставь в проекте 5 разрешение картинок 2k", {"image_resolution": "2k", "title": "x"}, {"image_resolution"}),
    ("сделай проект 5 вертикальным", {"aspect_ratio": "9:16", "image_resolution": "1k"}, {"aspect_ratio"}),
    ("поставь kling в проекте 5", {"video_generator": "kling_3_0", "image_resolution": "1k"}, {"video_generator"}),
    ("переименуй проект 5 в «Лисы»", {"title": "Лисы", "topic": "Лисы"}, {"title"}),
    # Ни одного названного ключа у одного проекта: шаг как был, решает карточка.
    ("сделай как в проекте 3", {"hero_mode": "hero", "image_resolution": "2k"}, {"hero_mode", "image_resolution"}),
])
def test_single_patch_keeps_named_keys(goal: str, params: dict[str, Any], kept: set[str]) -> None:
    step = PlanStep(action="project.patch_settings", params={"project_id": 5, **params}, mutating=True)
    out = scope_settings([step], goal)[0].params
    assert set(out) - {"project_id"} == kept


def test_mass_patch_never_rewrites_titles_without_words() -> None:
    plan = [PlanStep(action="project.patch_settings", params={"project_id": pid, "title": f"t{pid}",
                                                              "topic": f"t{pid}", "hero_mode": "hero"})
            for pid in (1, 2)]
    out = scope_settings(plan, "во всех проектах сделай как надо")
    assert [step.params for step in out] == [{"project_id": 1, "hero_mode": "hero"},
                                             {"project_id": 2, "hero_mode": "hero"}]


def test_model_word_without_kind_allows_both_generators() -> None:
    assert {"image_generator", "video_generator"} <= named_keys("поменяй модель в проекте 5", {})
    assert "video_generator" not in named_keys("поменяй модель картинок", {})


def test_unchanged_settings_are_found() -> None:
    from app.services.action_layer.builtin_wave5 import unchanged_settings

    project = {"image_generator": "gpt_image_2_vip", "aspect_ratio": "9_16", "title": "Лисы"}
    params = {"project_id": 5, "image_generator": "gpt_image_2_vip", "aspect_ratio": "9:16",
              "image_resolution": "1k", "title": "Лисы "}
    assert set(unchanged_settings(project, params)) == {"image_generator", "aspect_ratio", "title"}


@pytest.mark.asyncio
async def test_changed_settings_only_drops_equal_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.action_layer import builtin_wave5 as w5

    async def project(ctx: Any, pid: int) -> dict[str, Any]:
        return {"image_generator": "gpt_image_2_vip", "image_resolution": "2k"}

    monkeypatch.setattr(w5, "_get_project", project)
    out = await w5.changed_settings_only({"project_id": 5, "image_generator": "gpt_image_2_vip",
                                          "image_resolution": "1k"})
    assert out == {"project_id": 5, "image_resolution": "1k"}
    same = await w5.changed_settings_only({"project_id": 5, "image_generator": "gpt_image_2_vip"})
    assert same == {"project_id": 5, "image_generator": "gpt_image_2_vip"}


@pytest.mark.asyncio
async def test_supervisor_plan_has_only_the_image_model(token: str) -> None:
    del token
    plan = [PlanStep(action="project.patch_settings_costly", params={"project_id": pid, **_FULL},
                     mutating=True, risk="high") for pid in (1, 2)]
    actions = MockActions(risks={"project.patch_settings_costly": "high"})
    rt = build_runtime(actions=actions, validators=MockValidators(), router=ScriptedRouter(),
                       planner=FixedPlanner(plan), critic=ScriptedCritic(["approve"] * 3))
    set_runtime(rt)
    try:
        task_id = await rt.supervisor.start_task("ph6-mass", "во всех проектах поменяй модель картинок на дефолтную")
        await rt.supervisor.wait(task_id, timeout=5)
        saved = json.loads(rt.tasks.get(task_id)["plan_json"])
        assert [step["params"] for step in saved] == [{"project_id": 1, "image_generator": "gpt_image_2_vip"},
                                                      {"project_id": 2, "image_generator": "gpt_image_2_vip"}]
    finally:
        set_runtime(None)


# --- #227: «спасибо, все» — вежливое завершение ---

@pytest.mark.parametrize("text", ["спасибо, все", "Спасибо, всё!", "на этом все, спасибо", "все, спасибо большое",
                                  "спасибо, больше ничего не надо"])
def test_thanks_with_closing_words_is_thanks(text: str) -> None:
    assert small_talk_reply(text) == THANKS_TEXT


@pytest.mark.parametrize("text", ["спасибо, не надо", "спасибо, ничего не надо", "спасибо, ничего"])
def test_thanks_decline_is_a_decline(text: str) -> None:
    assert is_decline(text)


@pytest.mark.parametrize("text", ["спасибо", "спасибо, все", "ок спасибо"])
def test_plain_thanks_is_not_a_decline(text: str) -> None:
    assert not is_decline(text)


@pytest.mark.asyncio
async def test_thanks_no_while_card_waits_reminds_about_buttons(token: str) -> None:
    del token
    paid = PlanStep(action="paid_mock", params={"_wait": True, "_change_id": "chg-paid", "_cost": 1},
                    expected="paid", paid=True, mutating=True)
    rt = build_runtime(actions=MockActions(), validators=MockValidators(), router=ScriptedRouter(),
                       planner=FixedPlanner([paid]))
    set_runtime(rt)
    session = "talk-no-card"
    try:
        task_id = await rt.supervisor.start_task(session, "платный", project_id=13)
        await rt.supervisor.wait(task_id, timeout=5)
        assert rt.tasks.get(task_id)["status"] == TaskStatus.waiting_approval.value
        for text in ("спасибо, не надо", "спасибо, ничего не надо"):
            await rt.queue.accept(session, text)
            await rt.queue.drain(timeout=5)
            messages = [e for e in rt.bus.replay(session, 0) if e.type == "message"]
            assert messages[-1].payload["text"] == CARD_WAITING_TEXT
        assert rt.tasks.get(task_id)["status"] == TaskStatus.waiting_approval.value
    finally:
        set_runtime(None)


@pytest.mark.parametrize("text", ["останови все", "все", "спасибо, останови все", "стоп все"])
def test_stop_all_is_not_small_talk(text: str) -> None:
    assert small_talk_reply(text) is None


# --- #202, #204: ссылки с пробелом после скобки ---

def test_links_with_space_are_normalized() -> None:
    text = "[Скачать отчёт в HTML]( /api/projects/reports/projects_report_1.html) · [XLSX]( /api/projects/5/reports/a.xlsx )"
    assert normalize_links(text) == ("[Скачать отчёт в HTML](/api/projects/reports/projects_report_1.html) · "
                                     "[XLSX](/api/projects/5/reports/a.xlsx)")
    assert normalize_links("без ссылок (и скобки)") == "без ссылок (и скобки)"


@pytest.mark.asyncio
async def test_summary_from_model_has_clean_links() -> None:
    router = ScriptedRouter()
    router.script = ["Отчёт готов: 14 проектов.\n[Скачать отчёт в HTML]( /api/projects/reports/projects_report_1.html)"]
    note = StepNote("report.projects", "Отчёт", ok=True, text="14 проектов", readonly=True,
                    links=["[Скачать отчёт (html)](/api/projects/reports/projects_report_1.html)"])
    facts = TaskFacts(goal="отчет по всем проектам", status="done", steps=[note])
    text = await TaskSummarizer(router).summarize(facts, run_id=_RUN)
    assert "]( " not in text
    assert "[Скачать отчёт в HTML](/api/projects/reports/projects_report_1.html)" in text
    # Та же ссылка уже есть в ответе модели — вторую не добавляем.
    assert text.count("/api/projects/reports/projects_report_1.html") == 1


# --- #215: текст промта ноды в её описании ---

class _Ctx:
    def __init__(self, base: Any) -> None:
        self.base = base
        self.calls: list[str] = []

    async def call(self, method: str, path: str, **kwargs: Any) -> Any:
        self.calls.append(path)
        if path.endswith("/describe"):
            return {"text_ru": "Нода «Работа с GPT». Промт: default."}
        if isinstance(self.base, Exception):
            raise self.base
        return self.base


@pytest.mark.asyncio
async def test_node_describe_includes_prompt_text() -> None:
    """#215: «покажи какой промт стоит у первой работы с гпт» — был только «default», без текста."""
    from app.services.action_layer import builtin_node_edit as ne

    long_text = "Ты редактор листа. " * 200
    ctx = _Ctx({"variant": "default", "text": long_text, "text_chars": len(long_text), "source": "default"})
    out = await ne._describe(ctx, {"project_id": 7, "node_key": "n_excel_gpt_1"})
    assert "/change-control/prompt-base" in ctx.calls
    assert out["text_ru"].startswith("Нода «Работа с GPT». Промт: default.\nТекст промта «default»:\nТы редактор листа.")
    body = out["prompt_text"].split(":\n", 1)[1]
    assert len(body.split("\n(показаны")[0]) <= ne.PROMPT_SHOWN_CHARS
    assert f"из {len(long_text)} знаков)" in out["prompt_text"]


@pytest.mark.asyncio
async def test_node_describe_without_prompt_stays_as_is() -> None:
    from app.services.action_layer import builtin_node_edit as ne

    out = await ne._describe(_Ctx(RuntimeError("no prompt")), {"project_id": 7, "node_key": "n_img"})
    assert out == {"text_ru": "Нода «Работа с GPT». Промт: default."}
    missing = await ne._describe(_Ctx({"source": "missing", "text": ""}), {"project_id": 7, "node_key": "n"})
    assert "prompt_text" not in missing


def test_node_describe_fact_fits_prompt() -> None:
    from app.orchestrator.agents.question_context import fact_limit

    assert fact_limit("node.describe") >= 1500 + 1000


# --- #212: «смени модель на gpt» — картинки или ноды GPT? ---

@pytest.mark.parametrize("goal", ["смени модель на gpt в проекте 5 и запусти его", "поставь модель гпт в проекте 5"])
def test_ambiguous_gpt_model_asks(goal: str) -> None:
    text = gpt_model_clarify_text(goal)
    assert "картинок проекта (GPT Image)" in text and "нод «Работа с GPT»" in text
    assert "в проекте 5" in text


@pytest.mark.parametrize("goal", ["смени модель картинок на gpt в проекте 5", "поменяй модель у работы с gpt на дешевле",
                                  "смени модель на flux в проекте 5", "какие модели у нод gpt в проекте 5",
                                  "смени модель на gpt 5.6 sol в проекте 5", "смени модель на GPT 5.6 Sol",
                                  "поставь gpt-5.6-luna модель в проекте 3", "смени модель на гпт имидж в проекте 5",
                                  "смени модель на gpt terra"])
def test_clear_model_requests_do_not_ask(goal: str) -> None:
    assert gpt_model_clarify_text(goal) == ""


@pytest.mark.asyncio
async def test_planner_asks_instead_of_guessing_image_model() -> None:
    router = ScriptedRouter()
    router.script = []
    planner = RouterPlanner(router)
    plan = await planner.make_plan("смени модель на gpt в проекте 5 и запусти его", run_id=_RUN)
    assert plan == []
    assert planner.last_from_code is True
    assert "GPT Image" in planner.last_reason
    assert router.seen_messages == []


@pytest.mark.asyncio
async def test_ambiguous_gpt_model_answers_question_without_card(token: str) -> None:
    del token
    router = ScriptedRouter()
    rt = build_runtime(actions=MockActions(risks={"project.patch_settings": "medium"}), validators=MockValidators(),
                       router=router, planner=RouterPlanner(router), critic=ScriptedCritic([]))
    set_runtime(rt)
    try:
        task_id = await rt.supervisor.start_task("ph6-gpt", "смени модель на gpt в проекте 5 и запусти его")
        await rt.supervisor.wait(task_id, timeout=5)
        events = list(rt.bus.replay("ph6-gpt", 0))
        assert not [e for e in events if e.type == "approval_required"]
        texts = " ".join(str(e.payload.get("text") or "") for e in events if e.type == "message")
        assert "Какую модель поменять на GPT?" in texts, texts
    finally:
        set_runtime(None)


def test_create_project_keeps_only_named_settings() -> None:
    from app.orchestrator.agents.settings_scope import scope_settings
    from app.orchestrator.contracts import PlanStep

    step = PlanStep(action="project.create", params={"title": "тест", "topic": "тест", "aspect_ratio": "9:16",
                                                     "hero_mode": "auto", "image_resolution": "2k"})
    plain = scope_settings([step], "создай новый проект с темой «тест»")[0].params
    assert plain == {"title": "тест", "topic": "тест"}
    named = scope_settings([step], "создай вертикальный проект «тест» с героем в 2k")[0].params
    assert {"aspect_ratio", "hero_mode", "image_resolution"} <= set(named)
