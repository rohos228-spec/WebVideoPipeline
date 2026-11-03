"""Живой прогон фраз v5: модель картинок, критик и подтверждение, итоги без пересказа просьбы,
словарь кодов, вопрос о перезаписи полей, «из файла». Все вызовы модели — заглушки."""

from __future__ import annotations

import json

import pytest

from app.orchestrator.agents.action_catalog import ActionCatalog
from app.orchestrator.agents.critic import PlanCritic, confirmation_only
from app.orchestrator.agents.planner import RouterPlanner
from app.orchestrator.agents.question_context import GLOSSARY_RULE, QuestionContext, system_prompt
from app.orchestrator.agents.runtime import build_runtime, set_runtime
from app.orchestrator.agents.summarizer import FILE_CLARIFY, StepNote, TaskFacts, TaskSummarizer, drop_echo
from app.orchestrator.contracts import PlanStep
from tests.agents.mocks import MockActions, MockValidators, ScriptedCritic, ScriptedRouter

_RUN = "orch-task-1"


def _system(router: ScriptedRouter) -> str:
    return next(m["content"] for m in router.seen_messages[0] if m["role"] == "system")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "goal",
    [
        "смени модель картинок на gpt в проекте 5 и запусти его",
        "во всех проектах поменяй модель картинок на дефолтную",
    ],
)
async def test_planner_sees_image_models_default_and_gpt_word(goal: str) -> None:
    """#112, #113: планировщик не знал, что «gpt» и «дефолтная» — это gpt_image_2_vip, и возвращал пустой план."""
    router = ScriptedRouter()
    router.script = [
        json.dumps(
            {
                "steps": [
                    {
                        "action": "project.patch_settings",
                        "params": {"project_id": 5, "image_generator": "gpt_image_2_vip"},
                        "mutating": True,
                        "risk": "medium",
                    }
                ]
            }
        )
    ]
    planner = RouterPlanner(
        router,
        catalog=ActionCatalog(project_of=lambda task_id: None, projects=lambda: [(5, "Кот"), (7, "Пёс")]),
    )
    plan = await planner.make_plan(goal, run_id=_RUN)
    assert [step.action for step in plan] == ["project.patch_settings"]
    system = _system(router)
    assert "project.patch_settings" in system
    assert "По умолчанию (дефолт Studio) — nano_banana_2" in system
    assert "image_generator=gpt_image_2" in system
    assert "на каждый проект из списка «Проекты Студии»" in system


@pytest.mark.asyncio
async def test_model_change_then_launch_plan_reaches_a_card(token: str) -> None:
    """#112: смена модели и запуск — карточка подтверждения, а не «Не понял, что сделать»."""
    del token
    router = ScriptedRouter()
    router.script = [
        json.dumps(
            {
                "steps": [
                    {
                        "action": "project.patch_settings",
                        "params": {"project_id": 5, "image_generator": "gpt_image_2_vip"},
                        "mutating": True,
                        "risk": "medium",
                    },
                    {"action": "project.status", "params": {"project_id": 5}, "risk": "none"},
                    {
                        "action": "step.run_step",
                        "params": {"project_id": 5, "step_code": "img"},
                        "paid": True,
                        "mutating": True,
                        "risk": "high",
                    },
                ]
            }
        )
    ]
    rt = build_runtime(
        actions=MockActions(
            risks={"project.patch_settings": "medium", "project.status": "none", "step.run_step": "high"}
        ),
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=RouterPlanner(router, catalog=ActionCatalog(project_of=lambda task_id: None)),
        critic=ScriptedCritic(["approve"] * 4),
    )
    set_runtime(rt)
    try:
        task_id = await rt.supervisor.start_task(
            "ph5-model", "смени модель картинок на gpt в проекте 5 и запусти его", project_id=None
        )
        await rt.supervisor.wait(task_id, timeout=5)
        events = list(rt.bus.replay("ph5-model", 0))
        texts = [e.payload.get("text") or "" for e in events if e.type == "message"]
        assert not any("Не понял" in text for text in texts)
        cards = [e for e in events if e.type == "approval_required"]
        assert cards, [e.type for e in events]
        assert "project.patch_settings" in {
            step.get("action") for step in cards[0].payload.get("steps") or []
        }
    finally:
        set_runtime(None)


@pytest.mark.asyncio
async def test_mass_model_change_gets_real_project_ids() -> None:
    """#113: «во всех проектах» — планировщик видит настоящие id проектов, а не придумывает их."""
    router = ScriptedRouter()
    router.script = [
        json.dumps(
            {
                "steps": [
                    {
                        "action": "project.patch_settings",
                        "params": {"project_id": pid, "image_generator": "gpt_image_2_vip"},
                        "mutating": True,
                        "risk": "medium",
                    }
                    for pid in (5, 7)
                ]
            }
        )
    ]
    planner = RouterPlanner(
        router,
        catalog=ActionCatalog(project_of=lambda task_id: None, projects=lambda: [(5, "Кот"), (7, "Пёс")]),
    )
    plan = await planner.make_plan("во всех проектах поменяй модель картинок на дефолтную", run_id=_RUN)
    assert [step.params["project_id"] for step in plan] == [5, 7]
    system = _system(router)
    assert "Проекты Студии (project_id и название), других id нет: 5 «Кот»; 7 «Пёс»." in system


def test_mass_list_only_for_mass_request_and_honest_when_empty() -> None:
    calls: list[int] = []

    def projects() -> list[tuple[int, str]]:
        calls.append(1)
        return []

    catalog = ActionCatalog(project_of=lambda task_id: None, projects=projects)
    assert "других id нет" not in catalog.block("смени модель на gpt в проекте 5")
    assert calls == []
    block = catalog.block("поменяй модель картинок во всех проектах")
    assert "Списка проектов нет: id проектов не придумывай" in block


@pytest.mark.parametrize(
    "reason",
    [
        "План удаляет не тот проект: в просьбе проект 5, в плане 7; подтверждение пользователя не указано",
        "Шаг меняет модель во всех проектах, хотя просили только проект 5, без подтверждения",
        "Нет чтения статуса перед запуском и нет подтверждения пользователя",
        "Платный запуск без подтверждения и без оценки цены",
        "Шаг меняет модель во всех проектах без подтверждения",
    ],
)
def test_confirmation_with_real_problem_stays_revise(reason: str) -> None:
    """Замечание про подтверждение вместе с настоящей ошибкой — не одобрение."""
    assert not confirmation_only(reason)


@pytest.mark.parametrize(
    "reason",
    [
        "подтверждение действия в плане не указано",
        "Создание проекта изменяет состояние, а подтверждение этого действия в плане не указано",
        "Нет подтверждения пользователя",
        "Для изменяющего шага нужно подтверждение человека",
        "Шаг платный, подтверждение не указано",
    ],
)
def test_confirmation_only_remarks(reason: str) -> None:
    assert confirmation_only(reason)


@pytest.mark.asyncio
async def test_critic_keeps_revise_when_reason_has_wrong_project() -> None:
    router = ScriptedRouter()
    router.script = [
        json.dumps(
            {
                "verdict": "revise",
                "reason": "План меняет не тот проект: в просьбе 5, в плане 7; "
                "подтверждение пользователя не указано",
            }
        )
    ]
    critic = PlanCritic(router)
    plan = [
        PlanStep(
            action="project.patch_settings",
            params={"project_id": 7, "image_generator": "x"},
            mutating=True,
            risk="medium",
        )
    ]
    assert await critic.review(plan, run_id=_RUN) == "revise"
    assert "не тот проект" in critic.last_reason


@pytest.mark.asyncio
async def test_critic_confirmation_remark_is_not_a_revise() -> None:
    """#117: «подтверждение действия в плане не указано» — подтверждение спросит карточка."""
    router = ScriptedRouter()
    router.script = [
        json.dumps(
            {
                "verdict": "revise",
                "reason": "Создание проекта меняет состояние, но подтверждение действия в плане не указано",
            }
        )
    ]
    critic = PlanCritic(router)
    plan = [
        PlanStep(action="project.create", params={"title": "Тест многопоток"}, mutating=True, risk="medium")
    ]
    assert await critic.review(plan, run_id=_RUN) == "approve"
    assert "Не возвращай revise или reject из-за того, что в плане не указано подтверждение" in _system(
        router
    )
    # Подтверждение картинок перед видео — шаг конвейера, такое замечание остаётся.
    assert not confirmation_only("перед видео нет подтверждения картинок")
    assert not confirmation_only("перед запуском шага нет чтения состояния")


@pytest.mark.asyncio
async def test_create_project_goes_to_plain_card_not_critic_escalation(token: str) -> None:
    del token
    planner_router = ScriptedRouter()
    planner_router.answer = json.dumps(
        {
            "steps": [
                {
                    "action": "project.create",
                    "params": {"title": "Тест многопоток"},
                    "mutating": True,
                    "risk": "medium",
                }
            ]
        }
    )
    critic_router = ScriptedRouter()
    critic_router.answer = json.dumps(
        {
            "verdict": "revise",
            "reason": "Создание проекта изменяет состояние, а "
            "подтверждение этого действия в плане не указано",
        }
    )
    rt = build_runtime(
        actions=MockActions(risks={"project.create": "medium"}),
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=RouterPlanner(planner_router),
        critic=PlanCritic(critic_router),
    )
    set_runtime(rt)
    try:
        task_id = await rt.supervisor.start_task("ph5-create", "создай проект Тест многопоток", project_id=3)
        await rt.supervisor.wait(task_id, timeout=5)
        cards = [e for e in rt.bus.replay("ph5-create", 0) if e.type == "approval_required"]
        assert cards and cards[-1].payload["reason"] not in {"critic_escalation", "critic_reject"}
        assert planner_router.call_count == 1 and critic_router.call_count == 1
    finally:
        set_runtime(None)


@pytest.mark.asyncio
async def test_from_file_request_asks_for_topics(token: str) -> None:
    """#118: «создай 5 проектов из файла» — вопрос о темах, а не общий «Не понял»."""
    del token
    router = ScriptedRouter()
    router.answer = json.dumps(
        {
            "steps": [],
            "reason": "В доступных действиях нет чтения файла, а для запуска созданных проектов нужны их ID.",
        }
    )
    rt = build_runtime(
        actions=MockActions(),
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=RouterPlanner(router),
        critic=ScriptedCritic([]),
    )
    set_runtime(rt)
    try:
        task_id = await rt.supervisor.start_task(
            "ph5-file", "создай 5 проектов из файла и запусти", project_id=None
        )
        await rt.supervisor.wait(task_id, timeout=5)
        texts = [e.payload.get("text") for e in rt.bus.replay("ph5-file", 0) if e.type == "message"]
        assert texts and texts[-1] == FILE_CLARIFY
        assert "Не понял" not in texts[-1]
    finally:
        set_runtime(None)


_REPORT_TEXT = (
    "Отчёт по нодам проекта #5 «2 факта о пингвинах»: нод 18 (ждёт 15, готово 3), с ошибками 0. "
    "Файл отчёта готов: [Скачать отчёт (html)](/api/projects/5/reports/r.html) · "
    "[Скачать отчёт (xlsx)](/api/projects/5/reports/r.xlsx)"
)


def _report_facts(goal: str, action: str = "report.nodes", text: str = _REPORT_TEXT) -> TaskFacts:
    note = StepNote(
        action,
        "Отчёт по нодам проекта",
        ok=True,
        text=text,
        known=True,
        links=[
            "[Скачать отчёт (html)](/api/projects/5/reports/r.html)",
            "[Скачать отчёт (xlsx)](/api/projects/5/reports/r.xlsx)",
        ],
    )
    return TaskFacts(goal=goal, status="done", steps=[note])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "goal,answer",
    [
        (
            "отчет по гпт нодам проекта 5 в эксель",
            "скачай Excel-отчёт по GPT-нодам в проекте #5: **2 ноды, 0 готово**.",
        ),
        ("сводка по проектам 5 6 7 одной таблицей", "Покажи сводку по проектам #5, #6 и #7 одной таблицей."),
        ("отчет по кадрам проекта 7 с промтами", "Сформируй отчёт по кадрам с промтами в проекте 7."),
        ("выгрузи в html отчет по проекту 4", "Выгрузи HTML-отчёт по проекту 4."),
    ],
)
async def test_summary_that_restates_the_request_falls_back_to_the_result(goal: str, answer: str) -> None:
    """#102, #103, #124, #125: итог — что сделано, с числами и ссылками, а не просьба повелительным глаголом."""
    router = ScriptedRouter()
    router.answer = answer
    text = await TaskSummarizer(router).summarize(_report_facts(goal), run_id=_RUN)
    assert text.startswith("Отчёт по нодам проекта #5") and "нод 18" in text
    assert "(/api/projects/5/reports/r.html)" in text and "(/api/projects/5/reports/r.xlsx)" in text
    assert answer.split()[0] not in text
    assert "Пиши итог тем, что сделано" in router.seen_messages[0][0]["content"]


def test_echo_sentence_is_dropped_but_result_stays() -> None:
    """#116: «Снимков правок нет…» остаётся, «Откати промт в проекте 7.» уходит."""
    text = "Снимков правок **нет**, прежнее значение промта неизвестно.\n\nОткати промт в проекте 7."
    assert (
        drop_echo(text, "откати промт в проекте 7")
        == "Снимков правок **нет**, прежнее значение промта неизвестно."
    )
    kept = "Отчёт по проекту #7 готов.\n\nСкачать отчёт: [HTML](/a) · [Excel](/b)"
    assert drop_echo(kept, "сделай отчет по проекту 7") == kept
    advice = "Картинки не готовы. Запустите шаг картинок кнопкой."
    assert drop_echo(advice, "почему нет картинок") == advice


@pytest.mark.parametrize(
    "advice",
    [
        "Запусти его, когда будешь готов: напиши «запусти проект 15».",
        "Открой отчёт по ссылке выше.",
    ],
)
def test_next_step_advice_in_informal_form_stays(advice: str) -> None:
    text = "Проект #15 создан, кадров пока нет. " + advice
    assert drop_echo(text, "создай проект 15") == text


class _Reads:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def __call__(self, action, params, app):
        self.calls.append((action, dict(params)))
        if action == "report.nodes":
            return {
                "text_ru": "отчёт",
                "markdown": "Кто чьи поля перезаписал (работа с GPT по порядку на канвасе):\n"
                "- Работа с GPT 2: пока ничего не записала в кадры.",
            }
        return {"text_ru": f"факт {action}"}


@pytest.mark.asyncio
async def test_overwrite_question_reads_the_nodes_report() -> None:
    """#105: вопрос о перезаписи полей читает отчёт по нодам, где посчитано, кто чьи поля перезаписал."""
    reads = _Reads()
    facts = await QuestionContext(runner=reads).gather(
        "какие поля перезаписала вторая работа с gpt в проекте 7", 7
    )
    # Без файлов на диске: kind md отчёт html и xlsx не пишет.
    assert ("report.nodes", {"project_id": 7, "kind": "md"}) in reads.calls
    assert any("Кто чьи поля перезаписал" in fact.text for fact in facts)
    reads = _Reads()
    await QuestionContext(runner=reads).gather("сделай отчет по нодам проекта 7", 7)
    assert ("report.nodes", {"project_id": 7, "kind": "chat"}) in reads.calls


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "question,expected",
    [
        ("что такое img_pr", "img_pr — шаг конвейера «Промты картинок»"),
        (
            "что за нода n_excel_gpt_2",
            "n_excel_gpt_2 (ключ ноды на канвасе) — шаг конвейера «Работа с GPT» номер 2",
        ),
        ("что значит excel_gpt", "excel_gpt — шаг конвейера «Работа с GPT»: отправляет таблицу кадров в GPT"),
    ],
)
async def test_internal_codes_are_explained_in_plain_russian(question: str, expected: str) -> None:
    """#122: «что такое img_pr» — расшифровка из каталога шагов, а не «не могу сказать»."""
    facts = await QuestionContext(runner=_Reads()).gather(question, None)
    glossary = [fact for fact in facts if fact.action == "glossary"]
    assert glossary and expected in glossary[0].text
    prompt = system_prompt(facts, None)
    assert GLOSSARY_RULE in prompt and expected in prompt
