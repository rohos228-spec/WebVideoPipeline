"""Правка промта одним потоком чата и честный итог применения.

Живой прогон стенда 8812 (проект 7, задачи 360-366):

* после «допиши в конец промта … строку» и одобрения бот отвечал «Подготовил правку… Промт
  пока не изменён. Чтобы применить, напишите «примени правку промта»» — человеку нужна была
  вторая просьба. Теперь та же задача сразу ставит шаг применения со своей карточкой: риск
  high, отдельное одобрение, сам он не выполняется никогда;
* одобренное применение реально меняло промт, но задача кончалась «Не получилось: задача не
  выполнена» — ядро считало результат change.apply правкой без версии (``unversioned_edit``):
  у результата была ``version``, но не было ссылки на сохранённое прежнее значение;
* пустой план с понятной причиной планёра («правка ещё ожидает применения. Уточните, нужно ли
  отменить…») превращался в общий «Не понял, что сделать».

Все вызовы модели — заглушки.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.orchestrator.agents import summarizer
from app.orchestrator.agents.runtime import build_runtime, set_runtime
from app.orchestrator.contracts import PlanStep, Risk, TaskStatus, r21_result_problem
from tests.agents.mocks import FixedPlanner, MockValidators, ScriptedCritic, ScriptedRouter

PROPOSAL = "2094a641c3ce4f48bf16009cbfb26a83"
DIGEST = "549e612e7b50a2f1a49de9c9a825406a40b947d46bf1ba0b371f181df5be46c2"
TARGET = "project:7:n_excel_gpt_1:prompt"
EDIT_TEXT = (
    "Подготовил правку промта ноды n_excel_gpt_1 только для проекта #7, общий промт не меняется.\n"
    "Промт пока не изменён. Чтобы применить, напишите «примени правку промта»."
)
APPLIED_TEXT = (
    "Применил правку промта ноды «Работа с GPT 1» в проекте #7.\nДобавлено:\n+ Пиши коротко.\n"
    "Прежний вариант сохранён: если передумаете, напишите «откати правку промта»."
)


def _applied(snapshot_ref: bool = True) -> dict[str, Any]:
    body: dict[str, Any] = {
        "ok": True,
        "proposal_id": PROPOSAL,
        "status": "applied",
        "target": TARGET,
        "version": 2,
        "previous_version": 1,
        "digest": DIGEST,
        "text_ru": APPLIED_TEXT,
    }
    if snapshot_ref:
        body["snapshot_ref"] = f"config_version:{TARGET}@1"
    return body


class _Layer:
    """prompt.edit_text — правка без подтверждения слоя (medium), change.apply — разрушительное."""

    table = {
        "prompt.edit_text": {"risk": "medium", "read_only": False, "destructive": False, "paid": False},
        "change.apply": {"risk": "high", "read_only": False, "destructive": True, "paid": False},
    }

    def __init__(self, *, snapshot_ref: bool = True) -> None:
        self.calls: list[dict[str, Any]] = []
        self.snapshot_ref = snapshot_ref

    def metadata_registry(self) -> dict[str, dict[str, object]]:
        return {name: self.metadata_of(name) for name in self.table}

    def metadata_of(self, name: str) -> dict[str, object]:
        if name not in self.table:
            raise KeyError(name)
        return {"name": name, **self.table[name]}

    def human_plan_required(self, name: str) -> bool:
        return name == "change.apply"

    def project_required(self, name: str) -> bool:
        # change.apply — действие уровня студии (scope global): проект берётся из предложения.
        return name != "change.apply"

    def risk_of(self, name: str) -> Risk:
        return Risk(str(self.metadata_of(name)["risk"]))

    async def execute(self, name: str, params: dict, task_ctx: dict) -> dict[str, Any]:
        self.calls.append({"name": name, "params": dict(params), "ctx": dict(task_ctx)})
        if name == "prompt.edit_text":
            body = {
                "proposal_id": PROPOSAL,
                "status": "proposed",
                "digest": DIGEST,
                "target": TARGET,
                "node_key": "n_excel_gpt_1",
                "added_lines": ["Пиши коротко."],
                "apply_step": {
                    "action_id": "change.apply",
                    "params": {"proposal_id": PROPOSAL, "expected_digest": DIGEST},
                },
                "text_ru": EDIT_TEXT,
            }
            return {"status": "ok", "result": body, "cost": 0.0}
        return {"status": "ok", "result": _applied(self.snapshot_ref), "cost": 0.0}


def _edit(goal_append: str = "\nПиши коротко.") -> PlanStep:
    return PlanStep(
        action="prompt.edit_text",
        params={"project_id": 7, "node_key": "n_excel_gpt_1", "rationale": "короче", "append": goal_append},
        mutating=True,
        risk="medium",
    )


def _rt(layer: _Layer, plan: list[PlanStep]):
    return build_runtime(
        actions=layer,
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=FixedPlanner(plan),
        critic=ScriptedCritic(["approve"]),
    )


def _cards(rt: Any, session: str) -> list[dict[str, Any]]:
    return [dict(e.payload) for e in rt.bus.replay(session, 0) if e.type == "approval_required"]


def _summary(rt: Any, session: str) -> str:
    texts = [
        e.payload.get("text")
        for e in rt.bus.replay(session, 0)
        if e.type == "message" and e.payload.get("kind") == "task_summary"
    ]
    return str(texts[-1] if texts else "")


def _names(layer: _Layer) -> list[str]:
    return [call["name"] for call in layer.calls]


GOAL = "допиши в конец промта первой ноды работа с гпт в проекте 7 строку: Пиши коротко."


@pytest.mark.asyncio
async def test_prepared_edit_offers_apply_card_in_the_same_chat(token: str) -> None:
    del token
    layer = _Layer()
    rt = _rt(layer, [_edit()])
    set_runtime(rt)
    session = "pf-one-flow"
    try:
        task_id = await rt.supervisor.start_task(session, GOAL, project_id=7)
        await rt.supervisor.wait(task_id, timeout=5)
        first = _cards(rt, session)
        assert len(first) == 1 and first[0]["action"] == "prompt.edit_text"
        await rt.supervisor.grant_approval(session, change_id=first[0]["change_id"])
        await rt.supervisor.wait(task_id, timeout=5)
        # Правка подготовлена — и та же задача сразу спрашивает применение отдельной карточкой.
        assert _names(layer) == ["prompt.edit_text"]
        assert rt.tasks.get(task_id)["status"] == TaskStatus.waiting_approval.value
        cards = _cards(rt, session)
        assert len(cards) == 2
        apply = cards[-1]
        assert apply["action"] == "change.apply"
        assert apply["risk"] == "high" and apply["change_id"] != first[0]["change_id"]
        saved = rt.supervisor._stored_plan(task_id)
        assert [step.action for step in saved] == ["prompt.edit_text", "change.apply"]
        await rt.supervisor.grant_approval(session, change_id=apply["change_id"])
        await rt.supervisor.wait(task_id, timeout=5)
        assert _names(layer) == ["prompt.edit_text", "change.apply"]
        assert layer.calls[1]["params"] == {"proposal_id": PROPOSAL, "expected_digest": DIGEST}
        assert rt.tasks.get(task_id)["status"] == TaskStatus.done.value
        text = _summary(rt, session)
        # Итог — применение: без «промт пока не изменён» и без «Не получилось».
        assert text == APPLIED_TEXT
        assert "Не получилось" not in text and "пока не изменён" not in text
    finally:
        set_runtime(None)


@pytest.mark.asyncio
async def test_declined_apply_card_changes_nothing(token: str) -> None:
    del token
    layer = _Layer()
    rt = _rt(layer, [_edit()])
    set_runtime(rt)
    session = "pf-decline"
    try:
        task_id = await rt.supervisor.start_task(session, GOAL, project_id=7)
        await rt.supervisor.wait(task_id, timeout=5)
        await rt.supervisor.grant_approval(session, change_id=_cards(rt, session)[-1]["change_id"])
        await rt.supervisor.wait(task_id, timeout=5)
        card = _cards(rt, session)[-1]
        assert card["action"] == "change.apply"
        await rt.supervisor.reject_approval(session, change_id=card["change_id"])
        await rt.supervisor.wait(task_id, timeout=5)
        assert _names(layer) == ["prompt.edit_text"]
        assert rt.tasks.get(task_id)["status"] != TaskStatus.done.value
    finally:
        set_runtime(None)


@pytest.mark.asyncio
async def test_prepare_only_request_gets_no_apply_card(token: str) -> None:
    del token
    layer = _Layer()
    rt = _rt(layer, [_edit()])
    set_runtime(rt)
    session = "pf-prepare-only"
    try:
        goal = "подготовь правку промта ноды работа с гпт в проекте 7, но не применяй: допиши Пиши коротко."
        task_id = await rt.supervisor.start_task(session, goal, project_id=7)
        await rt.supervisor.wait(task_id, timeout=5)
        await rt.supervisor.grant_approval(session, change_id=_cards(rt, session)[-1]["change_id"])
        await rt.supervisor.wait(task_id, timeout=5)
        assert _names(layer) == ["prompt.edit_text"]
        assert len(_cards(rt, session)) == 1
        assert rt.tasks.get(task_id)["status"] == TaskStatus.done.value
    finally:
        set_runtime(None)


def test_apply_result_without_version_ref_was_the_unversioned_failure() -> None:
    """Причина «Не получилось» в задаче 364: тело с ``version`` без ссылки на прежнее значение."""
    assert r21_result_problem({"status": "ok", "result": _applied(snapshot_ref=False)}) == "unversioned_edit"
    assert r21_result_problem({"status": "ok", "result": _applied()}) is None


@pytest.mark.asyncio
async def test_approved_apply_is_reported_as_success(token: str) -> None:
    del token
    layer = _Layer()
    step = PlanStep(
        action="change.apply",
        params={"proposal_id": PROPOSAL, "expected_digest": DIGEST},
        mutating=True,
        risk="high",
    )
    rt = _rt(layer, [step])
    set_runtime(rt)
    session = "pf-apply-ok"
    try:
        task_id = await rt.supervisor.start_task(session, "примени правку промта")
        await rt.supervisor.wait(task_id, timeout=5)
        card = _cards(rt, session)[-1]
        assert card["action"] == "change.apply"
        await rt.supervisor.grant_approval(session, change_id=card["change_id"])
        await rt.supervisor.wait(task_id, timeout=5)
        row = rt.tasks.get(task_id)
        assert row["status"] == TaskStatus.done.value, row
        assert _summary(rt, session) == APPLIED_TEXT
        assert not [e for e in rt.bus.replay(session, 0) if e.type == "unversioned_edit"]
    finally:
        set_runtime(None)


class _AskingPlanner(FixedPlanner):
    def __init__(self, reason: str) -> None:
        super().__init__([])
        self.last_reason = reason
        self.last_parse_failed = False
        self.last_from_code = False
        self.last_response = None


REASON_362 = (
    "Откатить правку промта ноды n_excel_gpt_1 в проекте 7 нельзя: в доступном журнале нет снимка "
    "prompt_slot, а предложение 2094a641c3ce4f48bf16009cbfb26a83 ещё ожидает применения. Уточните, "
    "нужно ли отменить это неприменённое предложение или сначала применить его."
)


@pytest.mark.asyncio
async def test_planner_clarification_is_shown_instead_of_not_understood(token: str) -> None:
    del token
    rt = build_runtime(
        actions=_Layer(),
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=_AskingPlanner(REASON_362),
        critic=ScriptedCritic(["approve"]),
    )
    set_runtime(rt)
    session = "pf-clarify"
    try:
        task_id = await rt.supervisor.start_task(
            session, "откати правку промта первой работы с гпт в проекте 7"
        )
        await rt.supervisor.wait(task_id, timeout=5)
        text = _summary(rt, session)
        assert "Не понял" not in text and "Напишите номер проекта" not in text
        assert "Уточните, нужно ли отменить это неприменённое предложение" in text
        assert "2094a641" not in text and "n_excel_gpt_1" not in text and "prompt_slot" not in text
    finally:
        set_runtime(None)


@pytest.mark.parametrize(
    ("reason", "expected"),
    [
        (REASON_362, "Уточните, нужно ли отменить это неприменённое предложение или сначала применить его."),
        (
            "Для первой ноды «Работа с GPT» (n_excel_gpt_1) проекта 7 нет снимка правки промта: доступен "
            "только снимок смены модели №11. Уточните, какую именно правку откатить, или предоставьте "
            "snapshot_id вида prompt_slot.",
            "Уточните, какую именно правку откатить.",
        ),
    ],
)
def test_clarify_text_drops_ids_and_fields(reason: str, expected: str) -> None:
    text = summarizer.clarify_text(reason)
    assert text.endswith(expected)
    for raw in ("2094a641", "n_excel_gpt_1", "prompt_slot", "snapshot_id", "№11", "предоставьте"):
        assert raw not in text


def test_clarify_text_ignores_plain_refusals() -> None:
    # Без вопроса человеку — не уточнение: остаётся прежняя логика (и «Не понял», если нужно).
    assert (
        summarizer.clarify_text(
            "В контексте нет ожидающего предложения правки; передайте его "
            "proposal_id и expected_digest для применения."
        )
        == ""
    )
    assert summarizer.clarify_text("") == ""
