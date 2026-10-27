"""Живые формулировки чата: запуск в каталоге, точка остановки цепочки, пустой план «нужно
подтверждение», вопрос о статусе при идущей задаче."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

pytest.importorskip("app.orchestrator.actions")

from app.orchestrator.agents.action_catalog import ActionCatalog, wants_launch  # noqa: E402
from app.orchestrator.agents.planner import RouterPlanner, _system_prompt  # noqa: E402
from app.orchestrator.agents.project_nodes import chains_text, roster  # noqa: E402
from app.orchestrator.agents.runtime import build_runtime, set_runtime  # noqa: E402
from app.orchestrator.contracts import ClassifyResult, MessageMode, TaskStatus  # noqa: E402
from app.orchestrator.queue import MessageQueue, is_status_question  # noqa: E402
from app.services.action_layer import builtin_launch as bl  # noqa: E402
from tests.agents.mocks import MockActions, MockValidators, ScriptedCritic, ScriptedRouter  # noqa: E402

GROUP_RUN = ("в проекте 6 запусти ноду Границы сцен из группы Кадры и дальше по одной ноде группы "
             "до отчета, лимит 1 доллар")
NEXT_STEP = "в проектах 10,12,14 запусти следующий шаг, дальше они в очереди пойдут сами до видео"
ASKED = ("Требуется отдельное подтверждение человека перед платным запуском шага «Предметы»; "
         "подтвердите эти действия")


def _node(key: str, typ: str, label: str) -> dict:
    return {"id": key, "type": typ, "data": {"label": label}}


def _meta() -> dict:
    nodes = [_node("n_excel_gpt_fw_boundaries", "excel_gpt", "Границы сцен"),
             _node("n_excel_gpt_fw_script", "excel_gpt", "Каркас"),
             _node("n_excel_gpt_fw_report", "excel_gpt", "Отчёт: кадры"),
             _node("n_excel_gpt_1", "excel_gpt", "Работа с GPT"),
             _node("n_hero", "hero", "Персонажи")]
    chain = [n["id"] for n in nodes]
    edges = [{"source": a, "target": b} for a, b in zip(chain, chain[1:])]
    return {"canvas_graph": {"nodes": nodes, "edges": edges}}


def _ids(block: str) -> list[str]:
    return [json.loads(line)["id"] for line in block.splitlines() if line.startswith("{")]


@pytest.mark.parametrize("text", [
    GROUP_RUN,
    NEXT_STEP,
    "в проекте 6 запусти ноду Границы сцен из группы Кадры, дальше по цепочке группы до отчёта",
    "прогони в проекте 6 группу Кадры до отчёта",
    "перезапусти первую ноду работа с GPT у проектов 11, 12 и 13",
])
def test_launch_words_always_offer_step_run(text: str) -> None:
    assert wants_launch(text)
    block = ActionCatalog(max_catalog=12, project_of=lambda task_id: None).block(text)
    assert "step.run_step" in _ids(block)
    assert "step_code — шаг конвейера" in block


def test_read_only_question_does_not_force_launch() -> None:
    assert not wants_launch("покажи статус проекта 6")
    assert not wants_launch("пусть одновременно идут 10 проектов")


def test_planner_rules_cover_stop_point_and_approval() -> None:
    block = ActionCatalog(project_of=lambda task_id: 6).block(GROUP_RUN)
    system = _system_prompt([], "", block)
    assert "stop_after_node_key = ключ ноды, на которой остановиться" in system
    assert "«Только эту ноду» — stop_after_node_key = node_key" in system
    assert "из-за него план не отменяй и пустой steps не возвращай" in system
    assert "«следующий шаг по графу»" in system
    run = next(json.loads(line) for line in block.splitlines() if '"id":"step.run_step"' in line)
    assert "stop_after_node_key:str" in run["p"]


def test_critic_reads_stop_point_as_the_only_node_key() -> None:
    from app.orchestrator.agents.critic import _critic_system

    system = _critic_system([], "Стоп-ключ only_node взводится до старта шага.")
    assert "Стоп-ключ only_node из правила 9 в этом каталоге — параметр stop_after_node_key" in system


def test_gpt_chain_follows_arrows_until_a_non_gpt_node() -> None:
    assert bl.gpt_chain(_meta(), "n_excel_gpt_fw_boundaries") == [
        "n_excel_gpt_fw_boundaries", "n_excel_gpt_fw_script", "n_excel_gpt_fw_report", "n_excel_gpt_1"]
    assert bl.gpt_chain(_meta(), "n_excel_gpt_1") == ["n_excel_gpt_1"]


def test_chain_hint_names_the_report_node() -> None:
    meta = _meta()
    lines = chains_text(meta, roster(meta), ["n_excel_gpt_fw_boundaries"], GROUP_RUN)
    assert len(lines) == 1
    assert "n_excel_gpt_fw_report «Отчёт: кадры» → n_excel_gpt_1 «Работа с GPT»" in lines[0]
    assert chains_text(meta, roster(meta), ["n_excel_gpt_fw_boundaries"], "покажи ноды проекта") == []


class _Ctx:
    def __init__(self, meta: dict, status: str = "enrich_7_ready") -> None:
        self.project = {"id": 6, "title": "дельфины", "status": status, "meta": meta}
        self.calls: list[tuple[str, str, dict | None]] = []

    async def call(self, method: str, path: str, **kw):
        if method == "GET":
            return self.project
        self.calls.append((method, path, kw.get("json") or kw.get("params")))
        if method == "PATCH":
            self.project["meta"] = {**self.project["meta"], **kw["json"]["meta"]}
        return {}


_RUN = {"project_id": 6, "step_code": "excel_gpt", "node_key": "n_excel_gpt_fw_boundaries"}


async def test_stop_point_must_be_in_the_chain() -> None:
    ctx = _Ctx(_meta())
    assert await bl._pre_stop_point(ctx, {**_RUN, "stop_after_node_key": "n_excel_gpt_fw_report"}) is None
    assert await bl._pre_stop_point(ctx, dict(_RUN)) is None
    wrong = await bl._pre_stop_point(ctx, {**_RUN, "stop_after_node_key": "n_hero"})
    assert "не идёт по стрелкам" in wrong and "«Отчёт: кадры»" in wrong
    plain = await bl._pre_stop_point(ctx, {"project_id": 6, "step_code": "plan",
                                           "stop_after_node_key": "n_excel_gpt_fw_report"})
    assert "только для запуска ноды" in plain


async def test_run_arms_the_studio_stop_key_before_the_start(monkeypatch) -> None:
    async def _ok(ctx, params):
        return None
    monkeypatch.setattr(bl, "_verify_any_cost", _ok)
    ctx = _Ctx(_meta())
    out = await bl._run_any_step(ctx, {**_RUN, "stop_after_node_key": "n_excel_gpt_fw_report"})
    assert [c[0] for c in ctx.calls] == ["PATCH", "POST"]
    assert ctx.calls[0][2] == {"meta": {bl.STOP_META_KEY: "n_excel_gpt_fw_report"}}
    assert out["stop_after_node_key"] == "n_excel_gpt_fw_report"


async def test_run_without_stop_point_clears_a_stale_key(monkeypatch) -> None:
    async def _ok(ctx, params):
        return None
    monkeypatch.setattr(bl, "_verify_any_cost", _ok)
    stale = _Ctx({**_meta(), bl.STOP_META_KEY: "n_excel_gpt_fw_script"})
    await bl._run_any_step(stale, dict(_RUN))
    assert stale.calls[0] == ("PATCH", "/projects/6", {"meta": {bl.STOP_META_KEY: None}})
    clean = _Ctx(_meta())
    await bl._run_any_step(clean, dict(_RUN))
    assert [c[0] for c in clean.calls] == ["POST"]


async def test_poll_reports_the_chain_and_an_early_stop(monkeypatch) -> None:
    from app.services.action_layer.spec import JobState

    async def _done(ctx, params, started):
        return JobState(done=True, ok=True, result={"final_status": "enrich_7_ready"})
    monkeypatch.setattr(bl, "_node_run_poll", _done)
    params = {**_RUN, "stop_after_node_key": "n_excel_gpt_fw_report"}
    reached = await bl._poll_any_step(_Ctx(_meta()), params, {})
    assert "Цепочка нод от «Границы сцен» до «Отчёт: кадры» завершена" in reached.result["text_ru"]
    assert "не дошла" not in reached.result["text_ru"]
    early = _Ctx({**_meta(), bl.STOP_META_KEY: "n_excel_gpt_fw_report"})
    cut = await bl._poll_any_step(early, params, {})
    assert "до ноды «Отчёт: кадры» цепочка не дошла" in cut.result["text_ru"]
    assert cut.result["stopped_early"] is True
    assert early.calls == [("PATCH", "/projects/6", {"meta": {bl.STOP_META_KEY: None}})]


async def test_failed_chain_leaves_no_stop_key_for_the_studio(monkeypatch) -> None:
    from app.services.action_layer.spec import JobState

    async def _failed(ctx, params, started):
        return JobState(done=True, ok=False, error="нода завершилась с ошибкой")
    monkeypatch.setattr(bl, "_node_run_poll", _failed)
    ctx = _Ctx({**_meta(), bl.STOP_META_KEY: "n_excel_gpt_fw_report"})
    state = await bl._poll_any_step(ctx, {**_RUN, "stop_after_node_key": "n_excel_gpt_fw_report"}, {})
    assert state.ok is False
    assert ctx.project["meta"][bl.STOP_META_KEY] is None


def _runtime(planner, router):
    rt = build_runtime(actions=MockActions(), validators=MockValidators(), router=router,
                       planner=planner, critic=ScriptedCritic(["approve"]))
    set_runtime(rt)
    return rt


def _texts(rt, session: str) -> list[str]:
    return [str(e.payload.get("text") or "") for e in rt.bus.replay(session, 0) if e.type == "message"]


async def test_empty_plan_asking_for_approval_is_planned_again(token: str) -> None:
    del token
    router = ScriptedRouter()
    router.script = [
        json.dumps({"steps": [], "reason": ASKED}, ensure_ascii=False),
        json.dumps({"steps": [{"action": "project.status", "params": {"project_id": 10}, "paid": False,
                               "mutating": False, "risk": "none", "parallel_group": None}]}),
    ]
    rt = _runtime(RouterPlanner(router), router)
    try:
        task_id = await rt.supervisor.start_task("robust-asked", NEXT_STEP, project_id=10)
        await rt.supervisor.wait(task_id, timeout=10)
        assert rt.tasks.get(task_id)["status"] == TaskStatus.done.value
        second = router.seen_messages[1][1]["content"]
        assert "Не отказывай из-за подтверждения" in second
    finally:
        set_runtime(None)


async def test_repeated_approval_refusal_gets_a_plain_reason(token: str) -> None:
    del token
    router = ScriptedRouter()
    router.script = [json.dumps({"steps": [], "reason": ASKED}, ensure_ascii=False)] * 2
    rt = _runtime(RouterPlanner(router), router)
    try:
        task_id = await rt.supervisor.start_task("robust-asked-twice", NEXT_STEP, project_id=10)
        await rt.supervisor.wait(task_id, timeout=10)
        row = rt.tasks.get(task_id)
        assert row["status"] == TaskStatus.cancelled.value and row["error"] == "empty_plan"
        last = _texts(rt, "robust-asked-twice")[-1]
        assert "его я спрашиваю сам, карточкой перед запуском" in last and "Причина:" not in last
        assert "Требуется отдельное подтверждение" not in last
    finally:
        set_runtime(None)


@pytest.mark.parametrize("text", [
    "статус проекта 6",
    "как там проект 6, какая нода сейчас идет",
    "покажи прогресс по проекту 10",
    "что с проектом 12 сейчас",
    "как идет прогон проекта 6",
])
def test_status_phrases_are_questions(text: str) -> None:
    assert is_status_question(text)


@pytest.mark.parametrize("text", [
    "запусти проект 6 и покажи статус",
    "поставь проект 6 в очередь",
    "сделай отчёт по нодам проекта 6",
    "прогони проект 6 дальше, что с лимитом?",
])
def test_requests_are_not_status_questions(text: str) -> None:
    assert not is_status_question(text)


class _Bus:
    async def publish(self, *_a, **_k):
        return SimpleNamespace(seq=1)


class _Router:
    def __init__(self) -> None:
        self.classified: list[str] = []

    async def classify(self, text, **_k):
        self.classified.append(text)
        return ClassifyResult(tier="strong", mode=MessageMode.task, deterministic=False, confidence=0.6)


class _Supervisor:
    def __init__(self, active):
        self.active = active
        self.tasks: list[str] = []
        self.notes: list[str] = []

    def active_task(self, _s):
        return self.active

    def note_context(self, _s, text):
        self.notes.append(text)

    async def submit_task(self, _s, text, project_id=None):
        self.tasks.append(text)


async def _classify(active, text: str):
    sup, router = _Supervisor(active), _Router()
    queue = MessageQueue(_Bus(), router, sup)
    answered: list[str] = []

    async def _fast(session_id, text, tier, project_id=None):
        answered.append(text)
    queue._stream_fast = _fast
    await queue._classify("s", text, "run", None)
    return sup, router, answered


async def test_status_question_during_a_task_is_answered_at_once() -> None:
    sup, router, answered = await _classify(41, "статус проекта 6")
    assert answered == ["статус проекта 6"] and sup.tasks == [] and router.classified == []


async def test_status_without_a_task_still_goes_to_the_classifier() -> None:
    sup, router, answered = await _classify(None, "статус проекта 6")
    assert router.classified == ["статус проекта 6"] and sup.tasks == ["статус проекта 6"]


async def test_question_while_a_card_waits_is_answered_at_once() -> None:
    # Живой прогон: вопрос стоял 2 минуты в очереди за задачей, ждущей карточку.
    for text in ("какая модель стоит на сценарии в проекте 6", "сколько кадров в проекте 6?"):
        sup, router, answered = await _classify(207, text)
        assert answered == [text] and sup.tasks == [] and router.classified == [], text


async def test_request_with_question_during_a_task_is_not_answered_as_question() -> None:
    sup, router, answered = await _classify(207, "поставь модель подешевле, какая сейчас?")
    assert answered == [] and sup.tasks == ["поставь модель подешевле, какая сейчас?"]


def test_scenario_node_name_is_not_a_task_marker() -> None:
    from app.orchestrator.queue import classify_deterministic

    found = classify_deterministic("какая модель стоит на сценарии в проекте 6")
    assert found is not None and found.mode is MessageMode.append


def test_shot_numbers_written_as_strings_become_ints() -> None:
    from app.orchestrator.agents.planner import _schema_numbers
    from app.services.action_layer.spec import ensure_builtin_actions

    ensure_builtin_actions()
    out = _schema_numbers(
        "montage.move_image",
        {"project_id": "4", "from_frame": "1", "from_shot": "2", "to_frame": "2", "to_shot": "1", "note": "x"},
    )
    assert out["to_shot"] == 1 and out["from_frame"] == 1 and out["note"] == "x"
    assert _schema_numbers("no.such_action", {"a": "1"}) == {"a": "1"}


@pytest.mark.parametrize(
    ("text", "action"),
    [
        ("проект 12 кадр 6 сними снизу", "montage.set_coverage"),
        ("сделай вид снизу для кадра 8 в проекте 12", "montage.set_coverage"),
        ("в проекте 4 верни картинку из второго шота кадра 1 в первый шот кадра 2", "montage.move_image"),
        ("проект 4: картинку из кадра 2 положи вторым шотом в кадр 1", "montage.move_image"),
    ],
)
def test_angle_and_image_move_phrases_reach_the_catalog(text: str, action: str) -> None:
    from app.services.action_layer.planner_catalog import select_catalog
    from app.services.action_layer.spec import ensure_builtin_actions, registry

    ensure_builtin_actions()
    assert action in select_catalog(text, None, registry, 12).ids


@pytest.mark.parametrize("text", ["переименуй проект 12 в «Муравьи тест»", "назови проект 12 «Муравьи тест»"])
def test_rename_phrases_reach_project_settings(text: str) -> None:
    from app.services.action_layer.planner_catalog import select_catalog
    from app.services.action_layer.spec import ensure_builtin_actions, registry

    ensure_builtin_actions()
    assert "project.patch_settings" in select_catalog(text, None, registry, 12).ids
