"""Живой прогон m4 на линии port/today.

- «продолжи проект 10»: критик отклонил план, и вместо заблокированной карточки шага чат показал
  подтверждаемую карточку «Проверяющая модель против этого плана». Условие шага (кадры без данных
  сцены) так и не проверялось до одобрения. Теперь шаг, который условия запуска не пустят,
  сразу показывает свою карточку с причиной, а замечание критика остаётся предупреждением.
- «продолжи проект 6»: с ``enrich_2_ready`` воркер идёт по стрелкам холста прямо в промты картинок
  (вторая «Работа с GPT» последняя на холсте, хотя слотов в настройках три). Проверка
  продолжения считала следующим шагом третий слот и пропускала проект, а массовый запуск его
  отсекал. Теперь обе проверки сходятся.
Все вызовы модели — заглушки.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.orchestrator.agents.runtime import build_runtime, set_runtime
from app.orchestrator.contracts import PlanStep, TaskStatus
from app.services.action_layer import img_pr_guard as ipg
from tests.agents.mocks import FixedPlanner, MockActions, MockValidators, ScriptedCritic, ScriptedRouter

# --- «продолжи проект 6»: следующий шаг по стрелкам холста ------------------------------------


def _node(key: str, typ: str, slot: int | None = None) -> dict[str, Any]:
    data: dict[str, Any] = {}
    if slot is not None:
        data["slotIndex"] = slot
    return {"id": key, "type": typ, "data": data}


def _meta(*, third_slot: bool = False) -> dict[str, Any]:
    nodes = [
        _node("n_split", "split"),
        _node("n_excel_gpt_1", "excel_gpt", 1),
        _node("n_hero", "hero"),
        _node("n_items", "items"),
        _node("n_excel_gpt_2", "excel_gpt", 2),
        _node("n_image_prompts", "image_prompts"),
        _node("n_images", "images"),
    ]
    chain = ["n_split", "n_excel_gpt_1", "n_hero", "n_items", "n_excel_gpt_2"]
    if third_slot:
        nodes.append(_node("n_excel_gpt_3", "excel_gpt", 3))
        chain.append("n_excel_gpt_3")
    chain += ["n_image_prompts", "n_images"]
    edges = [{"source": a, "target": b} for a, b in zip(chain, chain[1:], strict=False)]
    return {"canvas_graph": {"nodes": nodes, "edges": edges}}


_RUNS_6 = [
    {"node_key": "n_split", "status": "done"},
    {"node_key": "n_excel_gpt_1", "status": "done"},
    {"node_key": "n_hero", "status": "pending"},
    {"node_key": "n_items", "status": "pending"},
    {"node_key": "n_excel_gpt_2", "status": "done"},
    {"node_key": "n_image_prompts", "status": "pending"},
    {"node_key": "n_images", "status": "pending"},
]


def test_meta_fixture_is_read_as_a_graph() -> None:
    assert ipg.scene_nodes_before_img_pr(_meta()) == ["n_excel_gpt_2"]
    assert ipg.queue_skips_scene(_meta(), _RUNS_6)


def test_continue_from_last_gpt_slot_on_canvas_goes_into_img_pr() -> None:
    """#6: слотов в настройках три, на холсте две ноды, от второй стрелка в промты картинок."""
    assert ipg.continue_skips_scene(
        _meta(), _RUNS_6, status="enrich_2_ready", auto_mode=False, enrich_slots=3
    )


def test_continue_with_next_gpt_slot_on_canvas_does_not_reach_img_pr() -> None:
    runs = [*_RUNS_6, {"node_key": "n_excel_gpt_3", "status": "pending"}]
    assert not ipg.continue_skips_scene(
        _meta(third_slot=True), runs, status="enrich_2_ready", auto_mode=False, enrich_slots=3
    )


def test_ready_slot_successor_reads_arrows() -> None:
    assert ipg._ready_slot_leads_to_img_pr(_meta(), "enrich_2_ready")
    assert not ipg._ready_slot_leads_to_img_pr(_meta(), "enrich_1_ready")
    assert not ipg._ready_slot_leads_to_img_pr(_meta(third_slot=True), "enrich_2_ready")
    assert not ipg._ready_slot_leads_to_img_pr(_meta(), "script_ready")
    assert not ipg._ready_slot_leads_to_img_pr({}, "enrich_2_ready")


# --- «продолжи проект 10»: критик против, но шаг уже заблокирован условием ---------------------

_META_ACTIONS = {
    "project.status": {"risk": "none", "read_only": True, "destructive": False, "paid": False},
    "project.continue_pipeline": {"risk": "high", "read_only": False, "destructive": False, "paid": True},
}
_SCENE = "шаг 1: условие img_pr_scene_input: у проекта #10 кадры 1, 2, 3 без данных сцены"
_CRITIC_WHY = "Запуск картинок запрещён, когда в проекте нет кадров"


class ContinueActions(MockActions):
    def __init__(self, blocked: bool) -> None:
        super().__init__()
        self.blocked = blocked
        self.cards: list[str] = []

    def metadata_of(self, name: str) -> dict[str, Any]:
        if name not in _META_ACTIONS:
            raise KeyError(name)
        return {"name": name, **_META_ACTIONS[name]}

    async def prepare_card(self, name: str, params: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
        self.cards.append(name)
        if self.blocked and name == "project.continue_pipeline":
            return {"blocked": True, "preview_errors": [_SCENE], "warnings": []}
        return {}


class RejectingCritic(ScriptedCritic):
    last_reason = _CRITIC_WHY


def _plan() -> list[PlanStep]:
    return [
        PlanStep(
            action="project.continue_pipeline",
            params={"project_id": 10},
            paid=True,
            mutating=True,
            risk="high",
        )
    ]


def _rt(actions: ContinueActions, verdicts: list[str]):
    return build_runtime(
        actions=actions,
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=FixedPlanner(_plan()),
        critic=RejectingCritic(verdicts),
    )


def _cards(rt: Any, session: str) -> list[dict[str, Any]]:
    return [dict(e.payload) for e in rt.bus.replay(session, 0) if e.type == "approval_required"]


@pytest.mark.asyncio
@pytest.mark.parametrize("verdicts", [["reject"], ["escalate"], ["revise", "revise", "revise"]])
async def test_critic_objection_shows_blocked_step_card(token: str, verdicts: list[str]) -> None:
    del token
    actions = ContinueActions(blocked=True)
    rt = _rt(actions, verdicts)
    set_runtime(rt)
    session = f"m4-block-{verdicts[0]}"
    try:
        task_id = await rt.supervisor.start_task(session, "продолжи проект 10", project_id=10)
        await rt.supervisor.wait(task_id, timeout=5)
        cards = _cards(rt, session)
        assert len(cards) == 1
        card = cards[0]
        assert card["reason"] not in {"critic_reject", "critic_escalation"}
        assert card["blocked"] is True
        assert card["preview_errors"] == [_SCENE]
        assert card["action"] == "project.continue_pipeline"
        if verdicts == ["reject"]:
            assert f"Проверяющая модель против этого плана: {_CRITIC_WHY}" in card["warnings"]
        assert rt.tasks.get(task_id)["status"] == TaskStatus.waiting_approval.value
        # Ничего не запускалось: исполнитель не вызывался.
        assert actions.calls == []
    finally:
        set_runtime(None)


@pytest.mark.asyncio
async def test_critic_reject_without_blocked_step_keeps_critic_card(token: str) -> None:
    del token
    actions = ContinueActions(blocked=False)
    rt = _rt(actions, ["reject"])
    set_runtime(rt)
    session = "m4-critic-card"
    try:
        task_id = await rt.supervisor.start_task(session, "продолжи проект 10", project_id=10)
        await rt.supervisor.wait(task_id, timeout=5)
        cards = _cards(rt, session)
        assert cards and cards[-1]["reason"] == "critic_reject"
        assert not cards[-1].get("blocked")
        assert actions.calls == []
    finally:
        set_runtime(None)
