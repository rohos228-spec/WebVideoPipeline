"""Живые находки: «продолжи проект 10» висела в круге «критик → планировщик» >150 с,
«смени модель … на gpt-5» отвечала «Не понял, что сделать». Все вызовы модели — заглушки."""

from __future__ import annotations

from typing import Any

import pytest

from app.orchestrator.agents import supervisor as sup
from app.orchestrator.agents.critic import PlanCritic, read_state_satisfied
from app.orchestrator.agents.runtime import build_runtime, set_runtime
from app.orchestrator.agents.summarizer import unknown_model_text
from app.orchestrator.contracts import PlanStep, TaskStatus
from tests.agents.mocks import FixedPlanner, MockActions, MockValidators, ScriptedCritic, ScriptedRouter

READ_META = {"risk": "none", "read_only": True, "destructive": False, "paid": False}
RESUME_META = {"risk": "high", "read_only": False, "destructive": False, "paid": True}
_META = {
    "project.status": READ_META,
    "node.describe": READ_META,
    "project.continue_pipeline": RESUME_META,
    "project.resume": RESUME_META,
    "step.run_step": RESUME_META,
    "node.set_model": {"risk": "medium", "read_only": False, "destructive": False, "paid": False},
}
_LIVE_WHY = (
    "Перед продолжением конвейера нужно повторно прочитать актуальное состояние проекта "
    "непосредственно перед запуском следующего шага"
)


class MetaActions(MockActions):
    def __init__(self, meta: dict[str, dict[str, Any]]) -> None:
        super().__init__()
        self.meta = meta

    def metadata_of(self, name: str) -> dict[str, Any]:
        if name not in self.meta:
            raise KeyError(name)
        return {"name": name, **self.meta[name]}


def _status(pid: int = 10) -> PlanStep:
    return PlanStep(action="project.status", params={"project_id": pid})


def _resume(pid: int = 10, action: str = "project.continue_pipeline") -> PlanStep:
    return PlanStep(action=action, params={"project_id": pid}, paid=True, mutating=True, risk="high")


# --- задача 334: план «статус → продолжение» без круга критика ---


def test_resume_after_read_plan_shapes() -> None:
    actions = MetaActions(_META)
    assert sup._resume_after_read_plan([_status(), _resume()], actions)
    # Без чтения статуса этого проекта — судит критик (карточка критика с условиями шага остаётся).
    assert not sup._resume_after_read_plan([_resume()], actions)
    assert not sup._resume_after_read_plan([PlanStep(action="node.describe", params={"project_id": 10, "node_key": "n"}), _resume()], actions)
    assert sup._resume_after_read_plan([_status(), _resume(action="project.resume")], actions)
    # Чтение другого проекта, два продолжения, продолжение не последним, лишний платный шаг — критик остаётся.
    assert not sup._resume_after_read_plan([_status(3), _resume()], actions)
    assert not sup._resume_after_read_plan([_resume(), _resume(11)], actions)
    assert not sup._resume_after_read_plan([_resume(), _status()], actions)
    run = PlanStep(action="step.run_step", params={"project_id": 10, "step_code": "img"}, paid=True,
                   mutating=True, risk="high")
    assert not sup._resume_after_read_plan([_status(), run, _resume()], actions)
    assert not sup._resume_after_read_plan([PlanStep(action="project.continue_pipeline", params={})], actions)
    # Продолжение без карточки (риск none и в реестре, и в плане) — не пропускаем мимо критика.
    quiet = MetaActions({**_META, "project.continue_pipeline": {**RESUME_META, "risk": "none", "paid": False}})
    step = PlanStep(action="project.continue_pipeline", params={"project_id": 10}, risk="none")
    assert not sup._resume_after_read_plan([_status(), step], quiet)


@pytest.mark.asyncio
async def test_continue_project_card_without_critic_round(token: str) -> None:
    del token
    actions = MetaActions(_META)
    critic = ScriptedCritic([])  # вызов критика упал бы: вердиктов нет
    rt = build_runtime(actions=actions, validators=MockValidators(), router=ScriptedRouter(),
                       planner=FixedPlanner([_status(), _resume()]), critic=critic)
    set_runtime(rt)
    try:
        task_id = await rt.supervisor.start_task("crl-continue", "продолжи проект 10", project_id=10)
        await rt.supervisor.wait(task_id, timeout=5)
        assert rt.tasks.get(task_id)["status"] == TaskStatus.waiting_approval.value
        cards = [e for e in rt.bus.replay("crl-continue", 0) if e.type == "approval_required"]
        assert cards and cards[-1].payload["reason"] not in {"critic_escalation", "critic_reject"}
        assert [c["name"] for c in actions.calls if c["name"] == "project.continue_pipeline"] == []
    finally:
        set_runtime(None)


def test_read_state_revise_satisfied_when_read_precedes() -> None:
    plan = [_status(), _resume()]
    assert read_state_satisfied(plan, _LIVE_WHY)
    assert read_state_satisfied(plan, "Перед запуском следующего шага нужно прочитать состояние проекта")
    assert not read_state_satisfied([_resume()], _LIVE_WHY)
    assert not read_state_satisfied([_status(3), _resume()], _LIVE_WHY)
    # Другое замечание рядом с чтением — revise остаётся.
    assert not read_state_satisfied(plan, "Нужно прочитать состояние и прогноз трат выше лимита")
    assert not read_state_satisfied(plan, "План удаляет кадры, сначала прочитай состояние")
    # Смена модели: нужно чтение этой же ноды, статус проекта не заменяет его.
    model = PlanStep(action="node.set_model", params={"project_id": 7, "node_key": "n_1", "model_id": "x"},
                     mutating=True, risk="medium")
    why = "Перед сменой нужно прочитать состояние ноды"
    assert not read_state_satisfied([_status(7), model], why)
    describe = PlanStep(action="node.describe", params={"project_id": 7, "node_key": "n_1"})
    assert read_state_satisfied([describe, model], why)


class _Reply:
    def __init__(self, text: str) -> None:
        self.text = text


class _OneReply:
    def __init__(self, text: str) -> None:
        self.text = text

    async def call(self, *args: Any, **kwargs: Any) -> _Reply:
        del args, kwargs
        return _Reply(self.text)


@pytest.mark.asyncio
async def test_critic_turns_redundant_read_revise_into_approve(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.orchestrator.agents import critic as critic_mod

    async def fake_call(router, tier, messages, **kwargs):
        del tier, messages, kwargs
        return await router.call()

    monkeypatch.setattr(critic_mod, "router_call", fake_call)
    monkeypatch.setattr(critic_mod, "server_forecast_limit", lambda: 100.0)
    monkeypatch.setattr(critic_mod, "ceiling_blocks", lambda limit: False)
    monkeypatch.setattr(critic_mod, "forecast_unknown", lambda plan: False)
    monkeypatch.setattr(critic_mod, "forecast_cost", lambda plan: 0.0)
    reply = '{"verdict": "revise", "reason": "' + _LIVE_WHY + '"}'
    critic = PlanCritic(_OneReply(reply))
    assert await critic.review([_status(), _resume()]) == "approve"
    assert await critic.review([_resume()]) == "revise"


def test_critic_system_knows_plan_order_and_resume_rechecks() -> None:
    from app.orchestrator.agents.critic import _critic_system

    text = _critic_system([])
    assert "строго по порядку" in text
    assert "project.continue_pipeline и project.resume сами перепроверяют" in text


@pytest.mark.asyncio
async def test_slow_revise_round_gives_card_instead_of_new_round(
    token: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """После revise, когда планирование идёт дольше бюджета, нового круга нет: карточка с замечанием."""
    del token
    clock = iter([0.0, 100.0, 100.0, 100.0, 100.0])
    monkeypatch.setattr(sup, "_monotonic", lambda: next(clock, 100.0))
    run = PlanStep(action="step.run_step", params={"project_id": 10, "step_code": "img"}, paid=True,
                   mutating=True, risk="high")
    planner_calls = []

    class _Planner(FixedPlanner):
        async def make_plan(self, goal, context="", **kwargs):
            planner_calls.append(goal)
            return await super().make_plan(goal, context, **kwargs)

    critic = ScriptedCritic(["revise", "revise", "revise"])
    critic.last_reason = "нужно прочитать состояние проекта"
    rt = build_runtime(actions=MetaActions(_META), validators=MockValidators(), router=ScriptedRouter(),
                       planner=_Planner([_status(), run]), critic=critic)
    set_runtime(rt)
    try:
        task_id = await rt.supervisor.start_task("crl-slow", "запусти картинки в проекте 10", project_id=10)
        await rt.supervisor.wait(task_id, timeout=5)
        assert len(planner_calls) == 1
        assert rt.tasks.get(task_id)["status"] == TaskStatus.waiting_approval.value
        cards = [e for e in rt.bus.replay("crl-slow", 0) if e.type == "approval_required"]
        assert cards
    finally:
        set_runtime(None)


# --- «смени модель первой работы с гпт в проекте 7 на gpt-5» ---

_TEXT_MODELS = [
    {"id": "claude-sonnet-5-5", "label": "Claude Sonnet 5.5", "kind": "text"},
    {"id": "gpt-6-sol", "label": "GPT 6 Sol", "kind": "text"},
    {"id": "gpt-6.1-sol", "label": "GPT 6.1 Sol", "kind": "text"},
    {"id": "gpt-6-astra", "label": "GPT 6 Astra", "kind": "text"},
    {"id": "gpt-5.6-sol", "label": "GPT 5.6 Sol", "kind": "text"},
    {"id": "gpt-6-luna", "label": "GPT 6 Luna", "kind": "text"},
    {"id": "gemini-3.8-flash", "label": "Gemini 3.8 Flash", "kind": "text"},
]
_GOAL = "смени модель первой работы с гпт в проекте 7 на gpt-5"


def _models(kind: str) -> list[dict]:
    return _TEXT_MODELS if kind == "text" else [{"id": "gpt-image-2", "label": "GPT Image 2"}]


@pytest.mark.parametrize("reason", [
    "Модели `gpt-5` нет в списке доступных для ноды `n_excel_gpt_1` проекта 7; уточните доступную "
    "модель, например `gpt-5.5` или `gpt-5.6-sol`.",
    "Модель «gpt-5» отсутствует в списке доступных моделей для первой ноды «Работа с GPT»; уточните "
    "точный вариант, например gpt-5.6-sol.",
])
def test_unknown_model_reason_becomes_question_with_closest_models(reason: str) -> None:
    text = unknown_model_text(reason, _GOAL, models_of=_models)
    assert text.startswith("Модели gpt-5 для этой ноды нет. Есть: gpt-5.6-sol, ")
    assert text.endswith("Какую поставить?")
    listed = text.split("Есть: ", 1)[1].split(". Какую", 1)[0].split(", ")
    assert len(listed) == 5 and all(item.startswith("gpt-") for item in listed)
    assert "gpt-5.5" not in listed  # модель из причины планировщика, которой нет в каталоге


def test_unknown_model_text_silent_on_other_reasons() -> None:
    assert unknown_model_text("Не нашёл проект 70", _GOAL, models_of=_models) == ""
    assert unknown_model_text("Модели «gpt-5.6-sol» нет в списке", _GOAL, models_of=_models) == ""


class _EmptyPlanner(FixedPlanner):
    def __init__(self, reason: str) -> None:
        super().__init__([])
        self.last_reason = reason


@pytest.mark.asyncio
async def test_unknown_model_answer_is_question_not_not_understood(
    token: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    del token
    monkeypatch.setattr("app.services.node_edit.allowed_models", _models)
    reason = ("Модели `gpt-5` нет в списке доступных для ноды `n_excel_gpt_1` проекта 7; уточните "
              "доступную модель, например `gpt-5.5` или `gpt-5.6-sol`.")
    rt = build_runtime(actions=MetaActions(_META), validators=MockValidators(), router=ScriptedRouter(),
                       planner=_EmptyPlanner(reason), critic=ScriptedCritic([]))
    set_runtime(rt)
    try:
        task_id = await rt.supervisor.start_task("crl-model", _GOAL, project_id=7)
        await rt.supervisor.wait(task_id, timeout=5)
        texts = [e.payload.get("text") for e in rt.bus.replay("crl-model", 0) if e.type == "message"]
        assert texts and texts[-1].startswith("Модели gpt-5 для этой ноды нет. Есть: gpt-5.6-sol")
        assert "Не понял" not in texts[-1] and "Напишите номер проекта" not in texts[-1]
        statuses = [e.payload.get("reason") for e in rt.bus.replay("crl-model", 0) if e.type == "agent_status"]
        assert "answered" in statuses
    finally:
        set_runtime(None)


# --- ревью: проверки критика без модели, белый список причины, вид ноды из состояния ---


class _CountingCritic(ScriptedCritic):
    def __init__(self, verdicts: list[str], reason: str) -> None:
        super().__init__(verdicts)
        self.calls = 0
        self.last_reason = reason

    async def review(self, plan, context="", **kwargs):
        self.calls += 1
        return await super().review(plan, context, **kwargs)


def _pricey() -> PlanStep:
    return PlanStep(action="project.continue_pipeline", params={"project_id": 10}, paid=True,
                    mutating=True, risk="high", estimated_cost=500.0)


def test_pre_approval_keeps_critic_spend_checks(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.orchestrator.agents import critic as critic_mod

    monkeypatch.setattr(critic_mod, "server_forecast_limit", lambda: 40.0)
    assert critic_mod.deterministic_verdict([_status(), _pricey()]) == ("revise", "прогноз трат выше лимита")
    assert not sup._critic_checks_clear([_status(), _pricey()])
    assert sup._critic_checks_clear([_status(), _resume()])
    queue = PlanStep(action="queue.enqueue_many", params={"project_ids": [5, 6]}, risk="high",
                     estimated_cost=500.0)
    assert not sup._critic_checks_clear([queue])


@pytest.mark.asyncio
async def test_over_limit_resume_goes_to_critic(token: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """[status, continue(estimated_cost=500)] при лимите 40: критик спрошен, его замечание на карточке."""
    del token
    from app.orchestrator.agents import critic as critic_mod

    monkeypatch.setattr(critic_mod, "server_forecast_limit", lambda: 40.0)
    critic = _CountingCritic(["revise", "revise", "revise"], "прогноз трат выше лимита")
    rt = build_runtime(actions=MetaActions(_META), validators=MockValidators(), router=ScriptedRouter(),
                       planner=FixedPlanner([_status(), _pricey()]), critic=critic)
    set_runtime(rt)
    try:
        task_id = await rt.supervisor.start_task("crl-pricey", "продолжи проект 10", project_id=10)
        await rt.supervisor.wait(task_id, timeout=5)
        assert critic.calls >= 1
        cards = [e for e in rt.bus.replay("crl-pricey", 0) if e.type == "approval_required"]
        assert cards
        assert "лимит" in str(cards[-1].payload)
    finally:
        set_runtime(None)


def test_resume_pre_approval_checks_every_param() -> None:
    actions = MetaActions(_META)
    # Чтение чужого проекта под другим ключом.
    other = PlanStep(action="project.status", params={"project": 99})
    assert not sup._resume_after_read_plan([_status(), other, _resume()], actions)
    assert not sup._resume_after_read_plan([other, _resume()], actions)
    # Лишние параметры продолжения: другие проекты, тенант, «до отчёта».
    wide = PlanStep(action="project.continue_pipeline", params={"project_id": 10, "project_ids": [11, 12],
                    "tenant_id": "other", "until_node": "report"}, paid=True, mutating=True, risk="high")
    assert not sup._resume_after_read_plan([_status(), wide], actions)
    for extra in ({"tenant_id": "x"}, {"until_node": "report"}, {"project_ids": [11]}):
        step = PlanStep(action="project.continue_pipeline", params={"project_id": 10, **extra},
                        paid=True, mutating=True, risk="high")
        assert not sup._resume_after_read_plan([_status(), step], actions)
    # Чтение без project_id перед продолжением — судит критик.
    assert not sup._resume_after_read_plan([PlanStep(action="project.status", params={}), _resume()], actions)
    assert sup._resume_after_read_plan([_status(), _status(), _resume()], actions)


_PLAN = [_status(), _resume()]


@pytest.mark.parametrize("reason", [
    "Нужно прочитать состояние; продолжение платное и дорогое, нужна оценка цены",
    "Прочитай состояние проекта 11 — пользователь просил проект 11, а в плане 10",
    "Прочитай состояние проекта 11 перед запуском",
    "Перед запуском прочитай статус: план продолжает не того клиента",
    "Сначала прочитай состояние: продолжение перезапишет и сотрёт готовые картинки",
    "Нужно прочитать статус, затем сбросить шаг; продолжение очистит результаты",
    "Прочитай состояние: проект 10 принадлежит другому арендатору/тенанту",
    "Прочитай статус: пользователь просил только одну ноду, а continue прогонит всю цепочку до отчёта",
    "прочитай статус; the plan spends $200 of credits",
    "Сначала прочитай статус проекта: на балансе $3, а продолжение потратит $25",
])
def test_read_state_filter_keeps_real_objections(reason: str) -> None:
    assert not read_state_satisfied(_PLAN, reason)


@pytest.mark.parametrize("reason", [
    _LIVE_WHY,
    "Перед запуском следующего шага нужно прочитать состояние проекта",
    "Нужно повторно прочитать статус проекта 10 перед project.continue_pipeline",
    "Сначала добавь project.status перед продолжением",
])
def test_read_state_filter_accepts_pure_read_requests(reason: str) -> None:
    assert read_state_satisfied(_PLAN, reason)


def test_read_state_filter_checks_steps_by_registry_and_scope() -> None:
    why = "Нужно прочитать состояние перед запуском"
    # Правка промта другого проекта без флага mutating — всё равно изменение без чтения проекта 12.
    edit = PlanStep(action="prompt.edit_text", params={"project_id": 12, "node_key": "x", "text": "..."})
    assert not read_state_satisfied([_status(), edit, _resume()], why)
    # Чтение без project_id не покрывает запуск проекта 55.
    run = PlanStep(action="step.run_step", params={"project_id": 55, "step_code": "img"}, paid=True,
                   mutating=True)
    assert not read_state_satisfied([PlanStep(action="project.status", params={}), run], why)
    assert read_state_satisfied([_status(55), run], why)
    # node.output другой ноды — не чтение состояния перед запуском шага.
    run10 = PlanStep(action="step.run_step", params={"project_id": 10, "step_code": "img"}, paid=True,
                     mutating=True)
    out = PlanStep(action="node.output", params={"project_id": 10, "node_key": "a"})
    assert not read_state_satisfied([out, run10], "Нужно прочитать статус")
    # Чтение с ключом другого проекта не покрывает.
    odd = PlanStep(action="project.status", params={"project_id": 10, "project_ids": [11]})
    assert not read_state_satisfied([odd, _resume()], why)


_CAT = {
    "text": [{"id": "gpt-5.6-sol", "label": "GPT 5.6"}, {"id": "gpt-4o", "label": "4o"}],
    "image": [{"id": "gpt-image-1", "label": "GPT Image"}, {"id": "flux-pro", "label": "Flux"}],
    "video": [{"id": "veo-3", "label": "Veo"}],
}


def _cat(kind: str) -> list[dict]:
    return _CAT[kind]


def test_unknown_model_does_not_mix_kinds() -> None:
    text = unknown_model_text("Модели `gpt-5` нет в списке доступных", "поставь gpt-5", models_of=_cat)
    assert text == "Модели gpt-5 для этой ноды нет. Какую модель поставить?"
    # Настоящий вид ноды из состояния важнее слов просьбы.
    real = unknown_model_text("Модели `gpt-5` нет в списке доступных", "поставь gpt-5", kind="text",
                              models_of=_cat)
    assert real == "Модели gpt-5 для этой ноды нет. Есть: gpt-5.6-sol, gpt-4o. Какую поставить?"


def test_unknown_model_text_node_about_pictures_gets_text_models() -> None:
    text = unknown_model_text("Модели `gpt-5` нет у ноды «Работа с GPT»",
                              "в ноде работа с gpt которая описывает картинки поставь gpt-5", models_of=_cat)
    assert "gpt-5.6-sol" in text and "gpt-image-1" not in text and "flux-pro" not in text
    real = unknown_model_text("Модели `gpt-5` нет у ноды", "в ноде которая описывает картинки поставь gpt-5",
                              kind="text", models_of=_cat)
    assert "gpt-image-1" not in real and "gpt-5.6-sol" in real


def test_unknown_model_guess_never_calls_existing_model_missing() -> None:
    # Вид угадан по словам («работа с gpt»), модель есть в каталоге картинок: не утверждаем, что её нет.
    assert unknown_model_text("Модели `gpt-image-1` нет у ноды",
                              "поставь в текстовую ноду работа с gpt модель gpt-image-1", models_of=_cat) == ""
    # Настоящий вид — текст: такой модели у ноды действительно нет.
    real = unknown_model_text("Модели `gpt-image-1` нет у ноды", "поставь модель gpt-image-1", kind="text",
                              models_of=_cat)
    assert real.startswith("Модели gpt-image-1 для этой ноды нет. Есть: ")


def test_unknown_model_name_skips_node_keys_and_redacts(monkeypatch: pytest.MonkeyPatch) -> None:
    assert unknown_model_text("Модель для ноды 'n_12' не найдена", "смени модель", models_of=_cat) == ""
    text = unknown_model_text("Модель для ноды 'n_12' не найдена: 'gpt-5'", "смени модель на gpt-5",
                              kind="text", models_of=_cat)
    assert text.startswith("Модели gpt-5 для этой ноды нет.")
    # Слово в кавычках, которого нет в просьбе, моделью не считаем.
    assert unknown_model_text("Модели `secretname` нет у ноды", "смени модель", models_of=_cat) == ""
    # Ответ проходит redact_text.
    import app.services.redact as redact_mod

    monkeypatch.setattr(redact_mod, "redact_text", lambda text: str(text).replace("gpt-5", "[скрыто]"))
    assert "gpt-5 " not in unknown_model_text("Модели `gpt-5` нет у ноды", "поставь gpt-5", kind="text",
                                             models_of=_cat)


def _canvas(typ: str) -> dict:
    return {"canvas_graph": {"nodes": [{"id": "n_excel_gpt_1", "type": typ, "data": {"label": "Работа с GPT"}}],
                             "edges": []}}


def test_node_model_kind_from_canvas() -> None:
    reason = "Модели `gpt-5` нет у ноды `n_excel_gpt_1` проекта 7"
    assert sup._node_model_kind(_canvas("excel_gpt"), reason, "") == "text"
    assert sup._node_model_kind(_canvas("images"), reason, "") == "image"
    assert sup._node_model_kind(None, reason, "") is None
    assert sup._node_model_kind(_canvas("images"), "Модели `gpt-5` нет у ноды `n_other`", "") is None


@pytest.mark.asyncio
async def test_unknown_model_answer_uses_real_node_kind(token: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Нода картинок по состоянию: в ответе модели картинок, хотя просьба похожа на текст."""
    del token

    async def _meta(project_id: int) -> dict:
        assert project_id == 7
        return _canvas("images")

    monkeypatch.setattr(sup, "_load_node_meta", _meta)
    monkeypatch.setattr("app.services.node_edit.allowed_models", _cat)
    reason = "Модели `gpt-5` нет в списке доступных для ноды `n_excel_gpt_1` проекта 7"
    rt = build_runtime(actions=MetaActions(_META), validators=MockValidators(), router=ScriptedRouter(),
                       planner=_EmptyPlanner(reason), critic=ScriptedCritic([]))
    set_runtime(rt)
    try:
        task_id = await rt.supervisor.start_task("crl-kind", "смени модель работы с гпт на gpt-5", project_id=7)
        await rt.supervisor.wait(task_id, timeout=5)
        texts = [e.payload.get("text") for e in rt.bus.replay("crl-kind", 0) if e.type == "message"]
        assert texts and texts[-1] == "Модели gpt-5 для этой ноды нет. Есть: gpt-image-1, flux-pro. Какую поставить?"
    finally:
        set_runtime(None)
