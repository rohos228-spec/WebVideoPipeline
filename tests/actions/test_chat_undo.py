"""«Верни как было» из чата: откат последней правки чата простыми словами."""

from __future__ import annotations

import copy
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.models import Project, ProjectStatus
from app.orchestrator import app_ref
from app.orchestrator.agents import approval_steps, project_nodes, summarizer
from app.orchestrator.agents.planner import RouterPlanner
from app.orchestrator.agents.question_context import DONE_EMPTY_RULE, DONE_RULE, DONE_TITLE, QuestionContext
from app.orchestrator.agents.task_store import TaskStore
from app.orchestrator.contracts import MessageMode, PlanStep
from app.orchestrator.queue import classify_deterministic
from app.services import orch_bridge
from app.services.action_layer import builtin_wave3, chat_undo, executor, store
from app.services.action_layer.spec import ensure_builtin_actions
from tests.action_layer_support import env

# Модели — из каталога WebVideoPipeline (main): gpt-5.5 / gpt-5.6-luna из int/chat-v1 здесь нет.
BASE = "/api/action-layer"
CHAT = "orchestrator:task-7"
NODES = [
    {"id": "n_script", "type": "script", "data": {"label": "Сценарий", "modelId": "gpt-5.6-sol"}},
    {"id": "n_review", "type": "excel_gpt", "data": {"label": "Проверка", "slotIndex": 1}},
]
PLAIN_PHRASES = ("верни как было", "отмени последнее", "откати последнее действие", "отмени последнее действие",
                 "откати последнюю правку", "откати", "верни прежнюю модель")
# Вопросы про откат: ответить словами, а не ставить карточку отката.
QUESTIONS = ("как вернуть прежнюю модель?", "как отменить последнее действие?",
             "что будет если откатить настройки?", "ты откатил модель?", "почему ты откатил модель?")
# «Отмени» про задачу: остановить работу, а не вернуть правку.
CANCEL_TASK = ("отмени последнюю задачу", "отмени предыдущую команду", "отмени это действие",
               "отмени операцию", "отмени последнюю генерацию", "отмени проверку", "отмени последнюю проверку")


@pytest.fixture(autouse=True)
def _builtin():
    ensure_builtin_actions()


@pytest.fixture(autouse=True)
def _bound_app(env, monkeypatch):
    monkeypatch.setitem(app_ref._APP, "app", env.app)


def step(action_id, /, **params):
    return {"action_id": action_id, "params": params}


async def _project(env, nodes=None) -> int:
    async with env.factory() as s:
        count = len((await s.scalars(select(Project.id))).all())
        nodes = copy.deepcopy(NODES if nodes is None else nodes)
        project = Project(topic="undo", slug=f"undo-{count}", status=ProjectStatus.new,
                          meta={"canvas_graph": {"workflow_id": 1, "nodes": nodes, "edges": []}})
        s.add(project)
        await s.commit()
        return int(project.id)


async def _run(env, steps, *, chat=True, operator=CHAT) -> dict:
    r = await env.client.post(f"{BASE}/plans/preview", json={"steps": steps})
    assert r.status_code == 200, r.text
    plan_id = r.json()["plan_id"]
    if r.json()["preview"]["needs_approval"]:
        assert (await env.client.post(f"{BASE}/plans/{plan_id}/approve")).status_code == 200
    assert (await env.client.post(f"{BASE}/plans/{plan_id}/run")).status_code == 200
    await executor.wait(plan_id)
    if chat:
        # План, который составил чат оркестратора: его создатель — задача чата.
        async with env.factory() as s:
            (await s.get(store.ActionPlan, plan_id)).operator = operator
            await s.commit()
    view = (await env.client.get(f"{BASE}/plans/{plan_id}")).json()
    assert view["status"] == "succeeded", [(s.get("error"), s.get("status")) for s in view["steps"]]
    return view["steps"][-1]["result"]


def _model(pid: int, model_id: str, node: str = "n_script") -> list[dict]:
    return [step("node.set_model_costly", project_id=pid, node_key=node, model_id=model_id)]


def _rollback_of(plan) -> int:
    assert plan is not None and plan.reason == "", plan
    assert [s["action"] for s in plan.steps] == ["history.rollback"]
    return plan.steps[0]["params"]["snapshot_id"]


async def test_plain_phrases_roll_back_the_last_chat_change(env) -> None:
    pid = await _project(env)
    await _run(env, _model(pid, "gpt-6-sol"))
    last = await _run(env, _model(pid, "gpt-6-luna"))
    for text in PLAIN_PHRASES + (f"верни как было в проекте {pid}", f"откати последнюю правку в проекте #{pid}"):
        assert _rollback_of(await chat_undo.resolve(text)) == last["snapshot_id"], text
        assert _rollback_of(await chat_undo.resolve(text, pid)) == last["snapshot_id"], text


async def test_back_to_previous_model_is_a_rollback_not_a_costly_change(env) -> None:
    pid = await _project(env)
    await _run(env, _model(pid, "gpt-6-sol"))
    last = await _run(env, _model(pid, "gpt-6-luna"))
    for text in (f"верни модель на ноде сценарий в проекте {pid} обратно на GPT 6 Sol",
                 f"поставь обратно GPT 6 Sol на сценарий в проекте {pid}"):
        assert _rollback_of(await chat_undo.resolve(text)) == last["snapshot_id"], text
    async with env.factory() as s:
        meta = (await s.get(Project, pid)).meta
    line = next(x for x in project_nodes.model_options_text(meta, "n_script").splitlines() if "GPT 6 Sol" in x)
    assert "прежняя модель" in line
    if "дороже" in line or "неизвестна" in line:
        # Цена растёт: вернуть прежнюю — та же смена на дороже, с карточкой.
        assert "node.revert_model" not in line and line.endswith("node.set_model_costly")
    else:
        assert "просьбе человека — node.revert_model, иначе node.set_model" in line
    # Два шага назад — не «последняя правка»: такую просьбу разбирает планёр.
    assert await chat_undo.resolve(f"верни модель на ноде сценарий в проекте {pid} на GPT 5.6 Sol") is None


async def test_only_chat_changes_in_scope_are_undone(env) -> None:
    first, second = await _project(env), await _project(env)
    await _run(env, _model(second, "gpt-6-sol"), chat=False)
    own = await _run(env, _model(first, "gpt-6-sol"))
    plan = await chat_undo.resolve("верни как было", second)
    assert plan is not None and plan.steps == [] and f"#{second}" in plan.reason
    assert "снимок" not in plan.reason and "snapshot" not in plan.reason
    assert _rollback_of(await chat_undo.resolve("верни как было")) == own["snapshot_id"]
    later = await _run(env, _model(second, "gpt-6-luna"))
    assert _rollback_of(await chat_undo.resolve("отмени последнее")) == later["snapshot_id"]
    assert _rollback_of(await chat_undo.resolve(f"отмени последнее в проекте {first}")) == own["snapshot_id"]
    assert _rollback_of(await chat_undo.resolve("отмени последнее", first)) == own["snapshot_id"]


async def test_change_returned_elsewhere_is_not_offered(env) -> None:
    pid = await _project(env)
    await _run(env, _model(pid, "gpt-6-sol"))
    await _run(env, [step("node.revert_model", project_id=pid, node_key="n_script")])
    plan = await chat_undo.resolve("верни как было", pid)
    assert plan is not None and plan.steps == []


async def test_rolled_back_change_is_not_offered_twice_and_undo_is_reversible(env) -> None:
    pid = await _project(env)
    change = await _run(env, _model(pid, "gpt-6-sol"))
    back = await _run(env, [step("history.rollback", snapshot_id=change["snapshot_id"])])
    # Последняя правка теперь сам возврат: «верни как было» возвращает правку обратно.
    assert _rollback_of(await chat_undo.resolve("верни как было", pid)) == back["reverse_snapshot_id"]


async def test_one_change_in_several_places_asks_which(env) -> None:
    pid = await _project(env)
    async with env.factory() as s:
        s.add(store.ActionPlan(id="p" * 32, operator=CHAT, plan_hash="h"))
        await s.commit()
    ids = [await store.save_snapshot(plan_id="p" * 32, step_index=0, action_id="node.set_model",
                                     project_id=pid, kind="node_model",
                                     target={"project_id": pid, "node_key": node},
                                     before="gpt-6-sol", after="gpt-6-luna", restorable=True)
           for node in ("n_script", "n_review")]
    plan = await chat_undo.resolve("верни как было", pid, current_of=_always)
    assert plan is not None and plan.steps == []
    assert "«Сценарий»" in plan.reason and "«Проверка»" in plan.reason and "Напишите" in plan.reason
    assert "n_script" not in plan.reason and "snapshot" not in plan.reason
    example = re.search(r"«([^»]+)»$", plan.reason).group(1)
    assert _rollback_of(await chat_undo.resolve(example, current_of=_always)) == ids[1]
    named = await chat_undo.resolve(f"верни прежнюю модель на ноде сценарий в проекте {pid}", current_of=_always)
    assert _rollback_of(named) == ids[0]


async def _always(_row) -> bool:
    return True


async def test_mass_model_phrase_plans_one_revert_for_all_projects(env) -> None:
    first, second = await _project(env), await _project(env)
    text = chat_undo.many_model_phrase([first, second], "Сценарий")
    plan = await chat_undo.resolve(text)
    assert plan is not None and plan.steps == [{
        "action": "node.revert_model_many", "params": {"project_ids": [first, second], "node_key": "n_script"},
        "paid": False, "mutating": True, "risk": "medium"}]


async def test_rollback_card_speaks_plain_russian(env) -> None:
    pid = await _project(env)
    await _run(env, _model(pid, "gpt-6-sol"))
    last = await _run(env, _model(pid, "gpt-6-luna"))
    card = await orch_bridge.prepare_card("history.rollback", {"snapshot_id": last["snapshot_id"]},
                                          {"app": env.app, "task_id": 7})
    title = f"Вернуть прежнюю модель «GPT 6 Sol» на ноде «Сценарий» в проекте #{pid}"
    assert card["title"] == title
    assert card["effects_reversible"] is True
    effects = " ".join(card["destructive_effects"])
    assert "«GPT 6 Sol» вместо «GPT 6 Luna»" in effects and "можно отменить" in effects
    assert "снимк" not in effects and "безвозврат" not in effects
    assert card["risk_tier"] == "destructive"
    plan = [PlanStep(action="history.rollback", params={"snapshot_id": last["snapshot_id"]}, mutating=True)]
    body = approval_steps.enrich(dict(card), plan, None)
    assert body["steps"][0]["title"] == title and body["steps"][0]["params_text"] == ""
    assert body["steps"][0]["risk"] == "high"
    bare = approval_steps.step_view(plan[0], None)
    assert "snapshot" not in bare["text"] and str(last["snapshot_id"]) not in bare["params_text"]


async def test_today_lists_the_chat_own_changes(env) -> None:
    pid = await _project(env)
    await _run(env, _model(pid, "gpt-6-sol"))
    await _run(env, _model(pid, "gpt-6-luna", node="n_review"), chat=False)
    lines = await chat_undo.today_changes()
    assert len(lines) == 1 and f"проект #{pid}" in lines[0] and "GPT 6 Sol" in lines[0]
    assert await chat_undo.today_changes(pid + 100) == []


async def test_what_did_you_do_today_reads_the_chat_changes() -> None:
    async def done_of(project_id, session_id):
        assert session_id == "s1"
        return ["- 14:05, проект #6: На ноде «Сценарий» теперь модель Grok 4.5 (была GPT 5.6 Sol)."]

    async def runner(action, params, app):
        return {"text_ru": "14 проектов"}

    ctx = QuestionContext(runner=runner, done_of=done_of)
    system = (await ctx.messages("что ты сделал за сегодня", None, None, "s1"))[0]["content"]
    assert DONE_RULE in system and f"[{DONE_TITLE}]" in system and "Grok 4.5" in system
    quiet = (await ctx.messages("сколько потратили сегодня", None, None, "s1"))[0]["content"]
    assert DONE_TITLE not in quiet


async def test_session_undoes_only_its_own_changes(env) -> None:
    pid = await _project(env)
    tasks = TaskStore()
    mine, other = tasks.insert("chat-a", "поставь модель", "done"), tasks.insert("chat-b", "поставь", "done")
    own = await _run(env, _model(pid, "gpt-6-sol"), operator=chat_undo.task_operator(mine))
    await _run(env, _model(pid, "gpt-6-luna", node="n_review"), operator=chat_undo.task_operator(other))
    later = tasks.insert("chat-a", "верни как было", "running")
    # Свежая правка соседнего чата не «последняя правка» этого чата.
    for task_id in (later, mine):
        assert _rollback_of(await chat_undo.plan_for_task("верни как было", task_id)) == own["snapshot_id"]
    lines = await chat_undo.today_changes(operators=chat_undo.session_operators("chat-a"))
    assert len(lines) == 1 and "GPT 6 Sol" in lines[0]
    assert await chat_undo.today_changes(operators=chat_undo.session_operators("chat-c")) == []
    empty = await chat_undo.plan_for_task("верни как было", tasks.insert("chat-c", "верни", "running"))
    assert empty is not None and empty.steps == []


async def test_card_says_irreversible_where_undo_leaves_no_snapshot(env) -> None:
    pid = await _project(env)
    async with env.factory() as s:
        s.add(store.ActionPlan(id="c" * 32, operator=CHAT, plan_hash="h"))
        await s.commit()
    sid = await store.save_snapshot(plan_id="c" * 32, step_index=0, action_id="node.set_agent", project_id=pid,
                                    kind="check_agent", target={"project_id": pid, "node_key": "n_review"},
                                    before={"source": "builtin"}, after={"source": "upload"}, restorable=True)
    card = await chat_undo.rollback_card({"snapshot_id": sid})
    assert card["title"].startswith("Вернуть прежнего агента проверки") and card["effects_reversible"] is False


async def test_questions_and_task_cancels_are_not_undo() -> None:
    rows = [_row("node_model", 11, node_key="n_script"), _row("check_agent", 13, node_key="n_review")]

    async def snapshots(project_id):
        return rows

    for text in QUESTIONS:
        assert not chat_undo.undo_phrase(text), text
        assert classify_deterministic(text).mode is MessageMode.append, text
        assert await chat_undo.resolve(text, 6, snapshots_of=snapshots, meta_of=_meta, current_of=_always) is None
    for text in CANCEL_TASK:
        assert not chat_undo.undo_phrase(text), text
        found = classify_deterministic(text)
        assert found is None or found.mode is not MessageMode.task, text
        assert await chat_undo.resolve(text, 6, snapshots_of=snapshots, meta_of=_meta, current_of=_always) is None
    # «Проверка» сама по себе — это работа ноды, а не агент проверки.
    assert chat_undo.kinds_of("отмени проверку") == set()
    assert chat_undo.kinds_of(chat_undo.AGENT_PHRASE) == {"check_agent"}
    # Уточнение текущей задачи остаётся уточнением.
    for text in ("вместо этого верни как было", "измени план: откати модель"):
        assert classify_deterministic(text).mode is MessageMode.steer, text


async def test_what_was_done_rule_matches_the_fact() -> None:
    async def nothing(project_id, session_id):
        return []

    async def broken(project_id, session_id):
        raise RuntimeError("db")

    empty = (await QuestionContext(done_of=nothing).messages("что ты сделал сегодня", None, None, "s"))[0]["content"]
    assert DONE_EMPTY_RULE in empty and DONE_RULE not in empty
    failed = (await QuestionContext(done_of=broken).messages("что ты менял", None, None, "s"))[0]["content"]
    assert DONE_RULE not in failed and DONE_EMPTY_RULE not in failed
    other = (await QuestionContext(done_of=nothing).messages("почему модель изменилась?", None, None, "s"))
    assert DONE_TITLE not in other[0]["content"]


def test_undo_requests_become_tasks_not_interrupts() -> None:
    for text in PLAIN_PHRASES + ("откати последнее действие", "верни как было в проекте 6"):
        found = classify_deterministic(text)
        assert found is not None and found.mode is MessageMode.task, text
    assert classify_deterministic("отмена").mode is MessageMode.interrupt
    assert classify_deterministic("стоп").mode is MessageMode.interrupt


async def test_planner_answers_undo_without_the_model() -> None:
    class NoRouter:
        async def call(self, *args, **kwargs):
            raise AssertionError("модель не нужна")

    async def undo(text, task_id):
        return chat_undo.UndoPlan([{"action": "history.rollback", "params": {"snapshot_id": 4},
                                    "mutating": True, "risk": "high"}])

    planner = RouterPlanner(NoRouter(), undo=undo)
    steps = await planner.make_plan("верни как было", task_id=1)
    assert [(s.action, s.params) for s in steps] == [("history.rollback", {"snapshot_id": 4})]


def _row(kind: str, row_id: int, **target) -> SimpleNamespace:
    return SimpleNamespace(id=row_id, kind=kind, project_id=6, plan_id=kind, before="gpt-6-sol",
                           after="gpt-6-luna", target={"project_id": 6, **target})


async def _meta(project_id):
    return {"canvas_graph": {"nodes": copy.deepcopy(NODES), "edges": []}}


# Самая свежая правка — модель: фраза без вида правки возвращает её.
_ROWS = (("node_model", 11, {"node_key": "n_script"}), ("prompt_slot", 12, {"node_key": "n_script", "slot_id": "main"}),
         ("check_agent", 13, {"node_key": "n_review"}), ("node_params", 14, {"node_key": "n_script"}))


def _expected_row(text: str) -> int:
    kinds = chat_undo.kinds_of(text)
    return next(row_id for kind, row_id, _target in _ROWS if not kinds or kind in kinds)


async def _all_kinds(project_id):
    return [_row(kind, row_id, **target) for kind, row_id, target in _ROWS]


def _suggested_phrases() -> list[str]:
    note = summarizer.StepNote("node.set_model", "Сменить модель", True, readonly=False, undo={"x": 1})
    texts = [summarizer._undo_line(note), builtin_wave3._AGENT_UNDO_RU,
             f"«{chat_undo.project_undo_phrase(6, 'промт')}»", f"«{chat_undo.project_undo_phrase(6)}»"]
    return [m for text in texts for m in re.findall(r"«([^»]+)»", text)]


async def test_every_suggested_undo_phrase_is_understood() -> None:
    meta = _meta
    phrases = _suggested_phrases()
    assert "верни как было" in phrases and len(phrases) == 4
    # После самого отката «откатить откат» не предлагаем.
    assert summarizer._undo_line(summarizer.StepNote("history.rollback", "Откат", True, undo={"x": 1})) == ""
    for text in phrases:
        assert classify_deterministic(text).mode is MessageMode.task, text
        plan = await chat_undo.resolve(text, snapshots_of=_all_kinds, meta_of=meta, current_of=_always)
        assert plan is not None and _rollback_of(plan) == _expected_row(text), text
    assert {_expected_row(text) for text in phrases} == {11, 12, 13}
    many = chat_undo.many_model_phrase([5, 6], "Сценарий")
    plan = await chat_undo.resolve(many, meta_of=meta)
    assert plan is not None and plan.steps[0]["action"] == "node.revert_model_many"


_QUOTED_UNDO = re.compile(r"«((?:верни|откати|отмени)\s[^»{]*)»")


async def test_undo_phrases_quoted_in_code_are_understood() -> None:
    root = Path(__file__).resolve().parents[2] / "app"
    found = {m for path in root.rglob("*.py") for m in _QUOTED_UNDO.findall(path.read_text(encoding="utf-8"))}
    # «верни только JSON, не Excel» — фраза промта main, не просьба об откате.
    found -= {"верни JSON", "верни файл", "верни только JSON, не Excel"}
    assert found
    assert [text for text in sorted(found) if not chat_undo.undo_phrase(text)] == []
    for text in sorted(found):
        plan = await chat_undo.resolve(text, 6, snapshots_of=_all_kinds, meta_of=_meta, current_of=_always)
        assert plan is not None and _rollback_of(plan) == _expected_row(text), text


async def test_mass_model_change_is_undone_project_by_project(env) -> None:
    first, second = await _project(env), await _project(env)
    out = await _run(env, [step("node.set_model_many", project_ids=[first, second], node_key="n_script",
                                model_id="gpt-6-sol")])
    assert [c["project_id"] for c in out["changed"]] == [first, second]
    # Каждый проект получил свой снимок, как при одиночной смене модели.
    sids = {c["project_id"]: c["snapshot_id"] for c in out["changed"]}
    assert all(sids.values()) and all(c["rollback_possible"] for c in out["changed"])
    for pid in (first, second):
        assert _rollback_of(await chat_undo.resolve(f"верни как было в проекте {pid}")) == sids[pid]
        assert _rollback_of(await chat_undo.resolve("верни как было", pid)) == sids[pid]
    # Без проекта: одна правка в двух проектах — чат спрашивает, что вернуть.
    which = await chat_undo.resolve("верни как было")
    assert which is not None and which.steps == [] and f"#{first}" in which.reason and f"#{second}" in which.reason
    # Фраза из ответа массовой правки по-прежнему возвращает всё разом.
    many = await chat_undo.resolve(chat_undo.many_model_phrase([first, second], "Сценарий"))
    assert many is not None and [s["action"] for s in many.steps] == ["node.revert_model_many"]
    # «Что ты сделал сегодня» в каждом проекте видит массовую правку.
    for pid in (first, second):
        lines = await chat_undo.today_changes(pid)
        assert len(lines) == 1 and "GPT 6 Sol" in lines[0] and f"#{pid}" in lines[0], lines


async def test_rollback_to_default_model_names_the_default(env) -> None:
    from app.services import node_edit as ne
    from app.services.vibecode_catalog import DEFAULT_TEXT_MODEL_ID

    default = ne.model_title(ne.find_catalog_model(DEFAULT_TEXT_MODEL_ID), DEFAULT_TEXT_MODEL_ID)
    bare = [{"id": "n_script", "type": "script", "data": {"label": "Сценарий"}}, NODES[1]]
    pid = await _project(env, bare)
    change = await _run(env, _model(pid, "gpt-6-sol"))
    card = await orch_bridge.prepare_card("history.rollback", {"snapshot_id": change["snapshot_id"]},
                                          {"app": env.app, "task_id": 7})
    assert card["title"] == f"Вернуть модель по умолчанию ({default}) на ноде «Сценарий» в проекте #{pid}"
    effects = " ".join(card["destructive_effects"])
    assert f"модель по умолчанию ({default}) вместо «GPT 6 Sol»" in effects
    back = await _run(env, [step("history.rollback", snapshot_id=change["snapshot_id"])])
    assert back["text_ru"] == (f"На ноде «Сценарий» вернул модель по умолчанию ({default}), "
                               "до этого была модель «GPT 6 Sol».")
    # Обратный откат: прежней была модель по умолчанию — её тоже называем.
    again = await orch_bridge.prepare_card("history.rollback", {"snapshot_id": back["reverse_snapshot_id"]},
                                           {"app": env.app, "task_id": 7})
    assert again["title"] == f"Вернуть прежнюю модель «GPT 6 Sol» на ноде «Сценарий» в проекте #{pid}"
    assert f"вместо модели по умолчанию ({default})" in " ".join(again["destructive_effects"])


async def test_restore_from_last_snapshot_is_the_chat_undo(env) -> None:
    pid, other = await _project(env), await _project(env)
    tasks = TaskStore()
    mine, foreign = tasks.insert("chat-snap", "поставь модель", "done"), tasks.insert("chat-x", "поставь", "done")
    own = await _run(env, _model(pid, "gpt-6-sol"), operator=chat_undo.task_operator(mine))
    await _run(env, _model(other, "gpt-6-sol"), operator=chat_undo.task_operator(mine))
    # Свежая правка соседней беседы в том же проекте — не «последний снимок» этого чата.
    await _run(env, _model(pid, "gpt-6-luna", node="n_review"), operator=chat_undo.task_operator(foreign))
    asking = tasks.insert("chat-snap", "восстанови", "running")
    for text in (f"восстанови прежние значения из последнего снимка в проекте {pid}",
                 f"откати к последнему снимку в проекте {pid}", f"верни из снимка в проекте {pid}",
                 f"восстанови из последнего снимка проект {pid}"):
        assert chat_undo.undo_phrase(text), text
        assert _rollback_of(await chat_undo.plan_for_task(text, asking)) == own["snapshot_id"], text
    # Снимок без просьбы вернуть — не откат.
    for text in ("сделай снимок проекта 6", "сколько снимков в проекте 6?", "что в последнем снимке?"):
        assert not chat_undo.undo_phrase(text), text
