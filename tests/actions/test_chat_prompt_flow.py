# ruff: noqa: F811  — фикстуры env/prompts_root импортируются и принимаются параметром
"""Правка промта из чата с откатом: подготовить → применить → прочитать → откатить → прочитать.

Живой прогон стенда 8812 (проект 7, нода n_excel_gpt_1, задачи 360-366):

* «примени правку промта» — «Не понял»: ожидающего предложения не было в контексте планёра;
* карточка применения говорила «предложение №2094a641 (контрольная сумма 549e612e…)»;
* «откати правку промта первой работы с гпт в проекте 7» — «Не понял»: журнал отката чата не
  знал версий change control, а слова «первой», «гпт» не сходились с нодой «Работа с GPT 1»;
* «откати правку», пока правка не применена, — общий «Не понял» вместо понятного ответа.

Здесь каждый шаг идёт через слой действий так же, как его запускает чат (план задачи чата,
подтверждение человека), а план «примени / откати / отмени» собирает код без модели.
"""

from __future__ import annotations

import copy
import re

import pytest

from app.models import Project, ProjectStatus
from app.orchestrator import app_ref
from app.orchestrator.contracts import r21_result_problem
from app.services import orch_bridge
from app.services.action_layer import chat_proposals, chat_undo, executor, store
from app.services.action_layer.spec import ensure_builtin_actions
from app.services.change_control import store as cs
from tests.action_layer_support import env  # noqa: F401
from tests.change_control_support import EDGES, NODES, prompts_root  # noqa: F401

AL = "/api/action-layer"
CHAT = "orchestrator:task-7"
NODE = "n_excel_gpt_1"
_HEX = re.compile(r"[0-9a-f]{8,}")


@pytest.fixture(autouse=True)
def _builtin():
    ensure_builtin_actions()


@pytest.fixture(autouse=True)
def _bound_app(env, monkeypatch):  # noqa: F811
    monkeypatch.setitem(app_ref._APP, "app", env.app)


def _meta() -> dict:
    nodes = copy.deepcopy(NODES)
    edges = copy.deepcopy(EDGES)
    for node in nodes:
        if node["id"] == "n_gpt":
            node["id"] = NODE
            node["data"]["label"] = "Работа с GPT"
    for edge in edges:
        if edge["target"] == "n_gpt":
            edge["target"] = NODE
    return {"canvas_graph": {"nodes": nodes, "edges": edges}}


@pytest.fixture
async def ccenv(env, prompts_root):  # noqa: F811
    async with env.engine.begin() as conn:
        await cs.ensure_change_control_schema(conn)
    env.prompts = prompts_root
    return env


async def _project(env, meta: dict | None = None) -> int:
    async with env.factory() as s:
        n = len((await s.execute(Project.__table__.select())).all())
        p = Project(
            topic="tema",
            slug=f"pf-{env.tmp_path.name[-6:]}-{n}",
            status=ProjectStatus.new,
            meta=meta or _meta(),
            prompt_overrides={},
        )
        s.add(p)
        await s.commit()
        return int(p.id)


async def _run(env, steps: list[dict], *, operator: str = CHAT) -> dict:
    """Шаг, как его запускает чат: план задачи чата, разрушительный — с подтверждением человека."""
    r = await env.client.post(f"{AL}/plans/preview", json={"steps": steps})
    assert r.status_code == 200, r.text
    plan_id = r.json()["plan_id"]
    if r.json()["preview"]["needs_approval"]:
        assert (await env.client.post(f"{AL}/plans/{plan_id}/approve")).status_code == 200
    assert (await env.client.post(f"{AL}/plans/{plan_id}/run")).status_code == 200
    await executor.wait(plan_id)
    async with env.factory() as s:
        (await s.get(store.ActionPlan, plan_id)).operator = operator
        await s.commit()
    view = (await env.client.get(f"{AL}/plans/{plan_id}")).json()
    assert view["status"] == "succeeded", [(s.get("error"), s.get("status")) for s in view["steps"]]
    return view["steps"][-1]["result"]


def _as_step(plan_step: dict) -> list[dict]:
    return [{"action_id": plan_step["action"], "params": plan_step["params"]}]


async def _prompt(env, pid: int) -> str:
    r = await env.client.get("/api/change-control/prompt-base", params={"project_id": pid, "node_key": NODE})
    assert r.status_code == 200, r.text
    return str(r.json()["text"])


async def _propose(env, pid: int, line: str = "Пиши коротко.") -> dict:
    params = {"project_id": pid, "node_key": NODE, "rationale": "короче", "append": line}
    return await _run(env, [{"action_id": "prompt.edit_text", "params": params}])


def _only(plan, action: str) -> dict:
    assert plan is not None and plan.reason == "", plan
    assert [s["action"] for s in plan.steps] == [action], plan.steps
    step = plan.steps[0]
    assert step["risk"] == "high" and step["paid"] is False and step["mutating"] is True
    return step


async def test_full_flow_propose_apply_tail_rollback_tail(ccenv) -> None:
    env = ccenv
    pid = await _project(env)
    original = await _prompt(env, pid)

    proposed = await _propose(env, pid)
    assert await _prompt(env, pid) == original, "предложение само промт не меняет"

    # «примени правку промта» — ровно то, что подсказал бот: одно ожидающее предложение.
    apply = _only(await chat_proposals.resolve("примени правку промта", operators=[CHAT]), "change.apply")
    assert apply["params"] == proposed["apply_step"]["params"]
    applied = await _run(env, _as_step(apply))
    # Результат применения — успех и для ядра оркестратора: прежнее значение — версия.
    assert r21_result_problem({"status": "ok", "result": applied}) is None
    assert applied["snapshot_ref"].endswith(f"@{applied['previous_version']}")
    assert "Применил правку промта ноды «Работа с GPT» в проекте" in applied["text_ru"]
    assert "+ Пиши коротко." in applied["text_ru"] and "«откати правку промта»" in applied["text_ru"]
    assert not _HEX.search(applied["text_ru"])
    changed = await _prompt(env, pid)
    assert changed.rstrip().endswith("Пиши коротко.")

    # Применять больше нечего.
    nothing = await chat_proposals.resolve("примени правку промта", operators=[CHAT])
    assert nothing.steps == [] and "нечего" in nothing.reason

    # Откат словами живого прогона: нода «первая работа с гпт», проект назван.
    for text in (
        f"откати правку промта первой работы с гпт в проекте {pid}",
        "верни промт как было",
        "откати последнее изменение",
        "откати правку промта",
    ):
        step = _only(await chat_undo.resolve(text, operators=[CHAT]), "change.rollback")
        assert step["params"] == {"target": applied["target"], "expected_version": applied["version"]}, text
        assert step["expected"].startswith("Откатить правку промта ноды «Работа с GPT»"), text
    rolled = await _run(env, _as_step(step))
    assert r21_result_problem({"status": "ok", "result": rolled}) is None
    assert "Откатил правку промта ноды «Работа с GPT»" in rolled["text_ru"]
    assert "убрано «Пиши коротко.»" in rolled["text_ru"] and "уберётся" not in rolled["text_ru"]
    assert await _prompt(env, pid) == original

    # Откат тоже обратим: «верни как было» снова применяет правку, а применённая версия не текущая.
    again = _only(await chat_undo.resolve("верни как было", operators=[CHAT]), "change.rollback")
    assert again["params"]["expected_version"] == rolled["version"]
    assert again["expected"].startswith("Снова применить отменённую правку промта")
    redone = await _run(env, _as_step(again))
    assert "Снова применил правку промта" in redone["text_ru"]
    assert "добавлено «Пиши коротко.»" in redone["text_ru"]
    assert (await _prompt(env, pid)).rstrip().endswith("Пиши коротко.")


async def test_cards_name_node_project_and_change_not_ids(ccenv) -> None:
    env = ccenv
    pid = await _project(env)
    proposed = await _propose(env, pid)
    params = proposed["apply_step"]["params"]
    card = await orch_bridge.prepare_card("change.apply", params, {"task_id": 7, "app": env.app})
    effect = " ".join(card["destructive_effects"])
    assert card["title"] == f"Применить правку промта ноды «Работа с GPT» в проекте #{pid}"
    assert "добавится «Пиши коротко.»" in effect and "откатить" in effect
    assert "«откати правку промта»" in effect
    for text in (card["title"], effect):
        assert not _HEX.search(text) and "контрольная" not in text and "билет" not in text
        assert "№" not in text and "предложени" not in text.lower()
    assert card["card_step"]["action"] == "change.apply" and card["card_step"]["params"] == params
    assert card["risk_tier"] == "destructive"

    applied = await _run(env, [{"action_id": "change.apply", "params": params}])
    back = {"target": applied["target"], "expected_version": applied["version"]}
    card = await orch_bridge.prepare_card("change.rollback", back, {"task_id": 7, "app": env.app})
    effect = " ".join(card["destructive_effects"])
    assert card["title"] == f"Откатить правку промта ноды «Работа с GPT» в проекте #{pid}"
    assert "станет таким, каким был до правки" in effect and "уберётся «Пиши коротко.»" in effect
    assert "этот откат тоже можно отменить" in effect
    assert "project:" not in card["title"] + effect and "верси" not in card["title"]
    assert card.get("cost_text") in (None, "бесплатно: платных шагов нет") or card["cost_estimate"] in (
        None,
        0,
    )


async def test_several_pending_asks_which_with_node_and_project(ccenv) -> None:
    env = ccenv
    first, second = await _project(env), await _project(env)
    await _propose(env, first, "Пиши коротко.")
    await _propose(env, second, "Пиши подробно.")
    plan = await chat_proposals.resolve("примени правку промта", operators=[CHAT])
    assert plan.steps == []
    assert f"ноды «Работа с GPT» в проекте #{first}" in plan.reason
    assert f"ноды «Работа с GPT» в проекте #{second}" in plan.reason
    assert "«Пиши коротко.»" in plan.reason and "«Пиши подробно.»" in plan.reason
    assert "например «примени правку промта в проекте" in plan.reason
    assert not _HEX.search(plan.reason)
    # Проект назван — его правка.
    step = _only(
        await chat_proposals.resolve(f"примени правку промта в проекте {second}", operators=[CHAT]),
        "change.apply",
    )
    view = await chat_proposals.describe_proposal(step["params"]["proposal_id"])
    assert view.project_id == second


async def test_session_pending_wins_over_other_chats(ccenv) -> None:
    from app.services.change_control import service

    env = ccenv
    first, second = await _project(env), await _project(env)
    await _propose(env, first, "Пиши коротко.")
    own = await _propose(env, second, "Пиши подробно.")
    async with env.factory() as s:
        row = await s.get(cs.ChangeProposal, own["proposal_id"])
        row.proposer = f"planner:{CHAT}"
        await s.commit()
    step = _only(await chat_proposals.resolve("примени правку", operators=[CHAT]), "change.apply")
    assert step["params"]["proposal_id"] == own["proposal_id"]
    assert (await service.get_proposal(own["proposal_id"]))["status"] == "proposed"


async def test_reject_unapplied_proposal(ccenv) -> None:
    from app.services.change_control import service

    env = ccenv
    pid = await _project(env)
    original = await _prompt(env, pid)
    proposed = await _propose(env, pid)
    # «Откати правку», пока она не применена: так и сказать, с готовыми фразами.
    plan = await chat_undo.plan_for_task("откати правку промта", None)
    assert plan is not None and plan.steps == []
    assert "ещё не применена" in plan.reason and "«примени правку промта»" in plan.reason
    assert "«отмени предложение правки»" in plan.reason and not _HEX.search(plan.reason)

    for text in ("отмени предложение правки", "не применяй правку", "отклони неприменённую правку"):
        step = _only(await chat_proposals.resolve(text, operators=[CHAT]), "change.reject")
        assert step["params"] == {"proposal_id": proposed["proposal_id"]}, text
    card = await orch_bridge.prepare_card("change.reject", step["params"], {"task_id": 7, "app": env.app})
    assert card["title"] == f"Отменить неприменённую правку промта ноды «Работа с GPT» в проекте #{pid}"
    out = await _run(env, _as_step(step))
    assert out["status"] == "rejected"
    assert out["text_ru"] == (
        f"Отменил неприменённую правку промта ноды «Работа с GPT» в проекте #{pid}. Промт не менялся."
    )
    assert (await service.get_proposal(proposed["proposal_id"]))["status"] == "rejected"
    assert await _prompt(env, pid) == original
    nothing = await chat_proposals.resolve("отмени предложение правки", operators=[CHAT])
    assert nothing.steps == [] and "отменять нечего" in nothing.reason


async def test_pending_proposals_reach_planner_context_without_project(ccenv) -> None:
    from app.orchestrator.agents import project_nodes

    env = ccenv
    pid = await _project(env)
    proposed = await _propose(env, pid)
    items = await project_nodes.load_proposals(None, [CHAT])
    assert [i["id"] for i in items] == [proposed["proposal_id"]]

    async def no_projects():
        return []

    nodes = project_nodes.ProjectNodes(projects_of=no_projects, project_of=lambda _task: None)
    block = await nodes.block("примени правку промта", None)
    assert project_nodes.ALL_PROPOSALS_HEAD in block
    assert f"proposal_id={proposed['proposal_id']}" in block and f"проект {pid}" in block


def test_phrases() -> None:
    for text in ("примени правку промта", "примени правку промта в проекте 7", "применяй", "внеси правку"):
        assert chat_proposals.apply_phrase(text) and not chat_proposals.reject_phrase(text), text
    for text in ("отмени предложение правки", "не применяй правку", "отклони неприменённую правку"):
        assert chat_proposals.reject_phrase(text) and not chat_proposals.apply_phrase(text), text
    # Новая правка или вопрос — это решает планёр, а не готовый план.
    for text in (
        "примени правку: допиши строку «Пиши коротко»",
        "как применить правку?",
        "допиши в конец промта строку",
        "откати правку промта",
        "отмени последнее",
    ):
        assert not chat_proposals.apply_phrase(text) and not chat_proposals.reject_phrase(text), text
    assert chat_undo.kinds_of("откати правку промта") >= {"prompt_version"}
