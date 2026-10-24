"""Задача 348 на стенде: «допиши в конец промта первой работы с гпт в проекте 7 строку: Пиши коротко.»

Планировщик дал prompt.edit_text с append и пустыми edits/text, схема отклонила шаг, подсказка
проверки («Исправь план: только id из каталога, значения параметров только из поля v каталога»)
ушла в контекст критика, и он вернул её revise к исправленному плану. Модели — заглушки.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.orchestrator.agents import supervisor as sup
from app.orchestrator.agents.critic import PlanCritic, catalog_params_only
from app.orchestrator.agents.planner import parse_plan
from app.orchestrator.agents.runtime import build_runtime, set_runtime
from app.orchestrator.contracts import PlanStep
from tests.agents.mocks import MockActions, MockValidators, ScriptedRouter

_LIVE_WHY = "В плане отсутствуют подтверждённые каталогом id и значения параметров из поля v"
_SCREEN = (
    "Проверка плана отклонила шаги: шаг 1 prompt.edit_text: $.edits: fewer than 1 items; шаг 1 "
    "prompt.edit_text: $.text: shorter than 40. Исправь план: только id из каталога, значения "
    "параметров только из поля v каталога."
)


def _edit(**params: Any) -> PlanStep:
    base = {"project_id": 7, "node_key": "n_excel_gpt_1", "rationale": "короче"}
    return PlanStep(action="prompt.edit_text", params={**base, **params}, mutating=True, risk="medium")


@pytest.mark.parametrize("reason", [
    _LIVE_WHY,
    "Используй только id из каталога и значения параметров из поля v",
    "Параметры шага не подтверждены каталогом",
])
def test_catalog_only_revise_is_recognised(reason: str) -> None:
    assert catalog_params_only(reason)


@pytest.mark.parametrize("reason", [
    "План меняет промт в проекте 8, а просили проект 7",
    "Прогноз трат выше лимита",
    "План удаляет кадры, проверь id из каталога",
    "node_key n_excel_gpt_2 не та нода, нужна первая",
    "Параметр append дописывает не тот текст: просили «Пиши коротко.»",
    "Шаг правит общий промт для всех клиентов, а не этот проект",
    "",
])
def test_catalog_filter_keeps_real_objections(reason: str) -> None:
    assert not catalog_params_only(reason)


class _OneReply:
    def __init__(self, text: str) -> None:
        self.text = text

    async def call(self, *args: Any, **kwargs: Any) -> Any:
        del args, kwargs
        return self


@pytest.mark.asyncio
async def test_critic_approves_plain_prompt_append_over_catalog_revise(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.orchestrator.agents import critic as critic_mod

    async def fake_call(router, tier, messages, **kwargs):
        del tier, kwargs
        fake_call.seen.append(messages)
        return await router.call()

    fake_call.seen = []
    monkeypatch.setattr(critic_mod, "router_call", fake_call)
    monkeypatch.setattr(critic_mod, "server_forecast_limit", lambda: 100.0)
    monkeypatch.setattr(critic_mod, "ceiling_blocks", lambda limit: False)
    monkeypatch.setattr(critic_mod, "forecast_unknown", lambda plan: False)
    monkeypatch.setattr(critic_mod, "forecast_cost", lambda plan: 0.0)
    critic = PlanCritic(_OneReply('{"verdict": "revise", "reason": "' + _LIVE_WHY + '"}'))
    describe = PlanStep(action="node.describe", params={"project_id": 7, "node_key": "n_excel_gpt_1"})
    assert await critic.review([_edit(append="Пиши коротко.")]) == "approve"
    assert await critic.review([describe, _edit(append="Пиши коротко.")]) == "approve"
    # Настоящее замечание остаётся revise, лимит трат — тоже.
    critic.router = _OneReply('{"verdict": "revise", "reason": "План меняет промт в проекте 8"}')
    assert await critic.review([_edit(append="Пиши коротко.")]) == "revise"
    monkeypatch.setattr(critic_mod, "forecast_cost", lambda plan: 1000.0)
    critic.router = _OneReply('{"verdict": "approve", "reason": ""}')
    assert await critic.review([_edit(append="Пиши коротко.")]) == "revise"


def test_planner_drops_empty_text_sources_next_to_append() -> None:
    raw = (
        '{"steps": [{"action": "prompt.edit_text", "params": {"project_id": 7, "node_key": "n_excel_gpt_1",'
        ' "rationale": "r", "append": "Пиши коротко.", "edits": [], "text": ""}}]}'
    )
    params = parse_plan(raw)[0].params
    assert params["append"] == "Пиши коротко." and "edits" not in params and "text" not in params
    # Нет ни одного заполненного поля — оставляем как есть, отказ даст схема сервера.
    empty = parse_plan(raw.replace('"Пиши коротко."', '""'))[0].params
    assert empty["edits"] == [] and empty["text"] == "" and empty["append"] == ""


def test_critic_context_drops_screen_notes_only() -> None:
    context = "история чата\n" + _SCREEN + "\n" + sup._PROMPT_PROPOSE + "\ncritic: revise — нет чтения"
    assert sup._critic_context(context) == "история чата\ncritic: revise — нет чтения"


class _SeqPlanner:
    def __init__(self, plans: list[list[PlanStep]]) -> None:
        self.plans = list(plans)
        self.contexts: list[str] = []

    async def make_plan(self, goal: str, context: str = "", **kwargs: Any) -> list[PlanStep]:
        del goal, kwargs
        self.contexts.append(context)
        return list(self.plans.pop(0) if len(self.plans) > 1 else self.plans[0])


class _RecordingCritic:
    def __init__(self) -> None:
        self.contexts: list[str] = []
        self.last_reason = ""
        self.last_response = None

    async def review(self, plan: list[PlanStep], context: str = "", **kwargs: Any) -> str:
        del plan, kwargs
        self.contexts.append(context)
        return "approve"


@pytest.mark.asyncio
async def test_screen_note_reaches_planner_but_not_critic(token: str) -> None:
    del token
    propose = PlanStep(action="change.propose", params={"kind": "prompt_text", "project_id": 7},
                       mutating=True, risk="medium")
    planner = _SeqPlanner([[propose], [_edit(append="Пиши коротко.")]])
    critic = _RecordingCritic()
    rt = build_runtime(actions=MockActions(), validators=MockValidators(), router=ScriptedRouter(),
                       planner=planner, critic=critic)
    set_runtime(rt)
    try:
        task_id = await rt.supervisor.start_task(
            "cpe-348", "допиши в конец промта первой работы с гпт в проекте 7 строку: Пиши коротко.",
            project_id=7,
        )
        await rt.supervisor.wait(task_id, timeout=5)
        assert len(planner.contexts) >= 2 and sup._PROMPT_PROPOSE in planner.contexts[1]
        assert critic.contexts and all("Проверка плана" not in c for c in critic.contexts)
        cards = [e for e in rt.bus.replay("cpe-348", 0) if e.type == "approval_required"]
        assert all(c.payload.get("reason") != "critic_escalation" for c in cards)
    finally:
        set_runtime(None)


def test_prompt_edit_card_names_the_project_not_this_project() -> None:
    from app.orchestrator.agents.approval_steps import node_title
    from app.services.action_layer.builtin_node_edit import prompt_edit_text_ru

    params = {"project_id": 7, "node_key": "n_excel_gpt_1", "append": "Пиши коротко."}
    title = node_title("prompt.edit_text", params, {"n_excel_gpt_1": "Работа с GPT 1"})
    assert title == "Предложить правку промта ноды «Работа с GPT 1» (только проект #7)"
    assert "этот проект" not in title
    text = prompt_edit_text_ru("n_excel_gpt_1", 10, 20, ["Пиши коротко."], [], 7)
    assert "только для проекта #7" in text and "этого проекта" not in text
