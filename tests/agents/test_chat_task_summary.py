from __future__ import annotations

import pytest

pytest.importorskip("app.orchestrator.actions")

from app.orchestrator.agents.action_catalog import ActionCatalog, catalog_entry  # noqa: E402
from app.orchestrator.agents.runtime import build_runtime, set_runtime  # noqa: E402
from app.orchestrator.agents.summarizer import (  # noqa: E402
    StepNote,
    TaskFacts,
    TaskSummarizer,
    deterministic_text,
    explain_failure,
)
from app.orchestrator.contracts import (  # noqa: E402
    AgentRole,
    MessageMode,
    PlanStep,
    TaskStatus,
    ValidatorResult,
)
from app.orchestrator.db.migrations import connect  # noqa: E402
from app.services.plain_ru import plain_ru  # noqa: E402
from app.services.spend_guard import BLOCK_KILL_SWITCH, PaidCallBlocked  # noqa: E402
from tests.agents.mocks import FixedPlanner, MockActions, MockValidators, ScriptedRouter  # noqa: E402

_READS = ("project.status", "diag.incidents", "logs.tail")


class _FactActions(MockActions):
    def __init__(self, fail: set[str] | None = None) -> None:
        super().__init__(
            risks={name: "none" for name in (*_READS, "diag.node_failure", "step.get_run_cost_basis")}
        )
        self.fail = fail or set()

    async def execute(self, name, params, task_ctx):
        self.calls.append({"name": name, "params": dict(params), "ctx": dict(task_ctx)})
        failed = name in self.fail
        body = {"error": "журнал недоступен"} if failed else {"text_ru": f"факт {name}"}
        return {
            "ok": not failed,
            "status": "failed" if failed else "ok",
            "data": body,
            "result": body,
            "cost": 0.0,
            "warnings": [],
            "error": "журнал недоступен" if failed else None,
            "change_id": None,
            "cc_change_id": None,
        }


class _SanityValidators(MockValidators):
    async def run(self, name, project_id, context):
        self.calls.append({"name": name, "project_id": project_id, "context": dict(context)})
        if name == "plan_sanity":
            from app.orchestrator.validators import plan_sanity

            found = plan_sanity.check(project_id, context)
            return ValidatorResult(
                name=name, passed=bool(found.passed), details=found.details, blocking=bool(found.blocking)
            )
        return ValidatorResult(name=name, passed=True, details="ok", blocking=False)


class _RecordingPlanner:
    def __init__(self, plans: list[list[PlanStep]]) -> None:
        self.plans = plans
        self.contexts: list[str] = []

    async def make_plan(self, goal, context="", **kwargs):
        self.contexts.append(context)
        index = min(len(self.contexts) - 1, len(self.plans) - 1)
        return list(self.plans[index])


class _BlockedRouter(ScriptedRouter):
    async def call(self, *args, **kwargs):
        raise PaidCallBlocked(
            BLOCK_KILL_SWITCH,
            "Платный вызов 52pay заблокирован аварийным рубильником. Запрос не отправлялся.",
            provider="52pay",
        )


class _BlockedPlanner:
    async def make_plan(self, goal, context="", **kwargs):
        raise PaidCallBlocked(
            BLOCK_KILL_SWITCH,
            "Платный вызов 52pay заблокирован аварийным рубильником. Запрос не отправлялся.",
            provider="52pay",
        )


def _reads(project_id: int = 4) -> list[PlanStep]:
    return [
        PlanStep(action="project.status", params={"project_id": project_id}, parallel_group="r"),
        PlanStep(action="diag.incidents", params={"project_id": project_id}, parallel_group="r"),
        PlanStep(action="logs.tail", params={"project_id": project_id, "kind": "errors"}, parallel_group="r"),
    ]


def _summaries(rt, session: str) -> list[dict]:
    return [
        event.payload
        for event in rt.bus.replay(session, 0)
        if event.type == "message" and event.payload.get("kind") == "task_summary"
    ]


def _stored(session: str) -> list[str]:
    conn = connect()
    try:
        # Соединение движка здесь — SQLAlchemy (app/orchestrator/db/engine.py): text() и :имя.
        from sqlalchemy import text

        rows = (
            conn.execute(
                text(
                    "SELECT content FROM orch_messages WHERE session_id = :sid AND role = 'assistant' ORDER BY id"
                ),
                {"sid": session},
            )
            .mappings()
            .all()
        )
    finally:
        conn.close()
    return [row["content"] for row in rows]


async def _run(rt, session: str, goal: str, project_id: int | None = 4) -> int:
    set_runtime(rt)
    task_id = await rt.supervisor.start_task(session, goal, project_id=project_id)
    await rt.supervisor.wait(task_id, timeout=10)
    return task_id


@pytest.mark.asyncio
async def test_read_only_task_ends_with_summary_of_facts(token: str) -> None:
    del token
    rt = build_runtime(
        actions=_FactActions(),
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=FixedPlanner(_reads()),
    )
    try:
        task_id = await _run(rt, "sum-reads", "почему встал шаг картинок? покажи ошибки из логов")
        assert rt.tasks.get(task_id)["status"] == TaskStatus.done.value
        summaries = _summaries(rt, "sum-reads")
        assert len(summaries) == 1
        text = summaries[0]["text"]
        assert summaries[0]["role"] == "assistant" and summaries[0]["status"] == "done"
        for name in _READS:
            assert plain_ru(f"факт {name}") in text
        assert "Вот что нашёл" in text
        assert _stored("sum-reads")[-1] == text.strip()
        kinds = [event.type for event in rt.bus.replay("sum-reads", 0)]
        assert kinds.index("message") < kinds.index("task_done")
    finally:
        set_runtime(None)


@pytest.mark.asyncio
async def test_summary_retells_facts_through_model_with_summarizer_role(token: str) -> None:
    del token
    router = ScriptedRouter()
    router.script = ["Шаг картинок встал: Kie ответил ошибкой 500, в логах три таких строки."]
    rt = build_runtime(
        actions=_FactActions(),
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=FixedPlanner(_reads()),
        summarizer=TaskSummarizer(router),
    )
    try:
        await _run(rt, "sum-model", "почему встал шаг картинок?")
        text = _summaries(rt, "sum-model")[0]["text"]
        assert text == "Шаг картинок встал: Kie ответил ошибкой 500, в логах три таких строки."
        assert router.last_call["agent_role"] == AgentRole.summarizer.value
        assert router.last_call["budget_scope"] == "internal" and router.last_call["run_id"]
        system = router.seen_messages[-1][0]["content"]
        assert "факт logs.tail" in system and "не придумывай" in system
        assert router.seen_messages[-1][1]["content"] == "почему встал шаг картинок?"
    finally:
        set_runtime(None)


@pytest.mark.asyncio
async def test_summary_falls_back_to_facts_when_model_is_blocked(token: str) -> None:
    del token
    rt = build_runtime(
        actions=_FactActions(),
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=FixedPlanner(_reads()),
        summarizer=TaskSummarizer(_BlockedRouter()),
    )
    try:
        await _run(rt, "sum-blocked", "что с проектом")
        text = _summaries(rt, "sum-blocked")[0]["text"]
        assert plain_ru("факт project.status") in text
        assert "аварийным рубильником" in text
    finally:
        set_runtime(None)


@pytest.mark.asyncio
async def test_failed_read_step_is_explained(token: str) -> None:
    del token
    rt = build_runtime(
        actions=_FactActions(fail={"logs.tail"}),
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=FixedPlanner(_reads()),
    )
    try:
        task_id = await _run(rt, "sum-fail-step", "покажи ошибки из логов")
        # Отказ одного чтения больше не роняет задачу из одних чтений: остальные факты
        # показываются, а отказ объясняется человеческим текстом.
        assert rt.tasks.get(task_id)["status"] == TaskStatus.done.value
        text = _summaries(rt, "sum-fail-step")[0]["text"]
        assert "Не получилось «" in text and "журнал недоступен" in text
    finally:
        set_runtime(None)


@pytest.mark.asyncio
async def test_validator_errors_go_back_to_planner_and_fixed_plan_runs(token: str) -> None:
    del token
    bad = [PlanStep(action="step.get_run_cost_basis", params={"project_id": 4, "step_code": "images"})]
    good = [PlanStep(action="step.get_run_cost_basis", params={"project_id": 4, "step_code": "img"})]
    planner = _RecordingPlanner([bad, good])
    actions = _FactActions()
    rt = build_runtime(
        actions=actions, validators=_SanityValidators(), router=ScriptedRouter(), planner=planner
    )
    try:
        task_id = await _run(rt, "sum-revise", "запусти генерацию картинок для этого проекта")
        assert rt.tasks.get(task_id)["status"] == TaskStatus.done.value
        assert len(planner.contexts) == 2
        assert "step_code" in planner.contexts[1] and "img" in planner.contexts[1]
        assert [call["params"]["step_code"] for call in actions.calls] == ["img"]
    finally:
        set_runtime(None)


@pytest.mark.asyncio
async def test_validator_rejection_after_two_revisions_tells_user(token: str) -> None:
    del token
    bad = [PlanStep(action="step.get_run_cost_basis", params={"project_id": 4, "step_code": "images"})]
    planner = _RecordingPlanner([bad])
    actions = _FactActions()
    rt = build_runtime(
        actions=actions, validators=_SanityValidators(), router=ScriptedRouter(), planner=planner
    )
    try:
        task_id = await _run(rt, "sum-reject", "запусти генерацию картинок для этого проекта")
        row = rt.tasks.get(task_id)
        assert row["status"] == TaskStatus.failed.value and row["error"] == "validator_blocked"
        assert len(planner.contexts) == 3
        assert actions.calls == []
        text = _summaries(rt, "sum-reject")[0]["text"]
        assert text.startswith("Не получилось: проверка плана отклонила шаги")
        assert "недопустимое значение «images» (шаг конвейера)" in text
        assert "Что можно сделать" in text
        assert "step.get_run_cost_basis" not in text
    finally:
        set_runtime(None)


@pytest.mark.asyncio
async def test_unknown_action_gets_a_revision_before_block(token: str) -> None:
    del token
    import app.orchestrator.actions as live_actions
    from app.orchestrator.agents.wiring import ActionsBridge, ModuleActions

    planner = _RecordingPlanner([[PlanStep(action="images.generate", params={"project_id": 4})]])
    rt = build_runtime(
        actions=ActionsBridge(ModuleActions(live_actions)),
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=planner,
    )
    try:
        task_id = await _run(rt, "sum-unknown", "сделай картинки")
        assert rt.tasks.get(task_id)["error"] == "not_in_whitelist"
        assert len(planner.contexts) == 3
        assert "images.generate" in planner.contexts[1]
        assert "нет среди разрешённых" in _summaries(rt, "sum-unknown")[0]["text"]
    finally:
        set_runtime(None)


@pytest.mark.asyncio
async def test_planner_blocked_by_paid_gate_reports_reason(token: str) -> None:
    del token
    rt = build_runtime(
        actions=_FactActions(),
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=_BlockedPlanner(),
    )
    try:
        task_id = await _run(rt, "sum-gate", "что с проектом")
        assert rt.tasks.get(task_id)["error"] == "drive_failed"
        text = _summaries(rt, "sum-gate")[0]["text"]
        assert "Модель не вызвана" in text and "аварийным рубильником" in text
    finally:
        set_runtime(None)


@pytest.mark.asyncio
async def test_chat_answer_blocked_by_paid_gate_is_explained(token: str) -> None:
    del token
    rt = build_runtime(actions=MockActions(), validators=MockValidators(), router=_BlockedRouter())
    set_runtime(rt)
    try:
        await rt.queue.accept("ans-gate", "как дела?", mode=MessageMode.append)
        await rt.queue.drain()
        texts = [event.payload["text"] for event in rt.bus.replay("ans-gate", 0) if event.type == "message"]
        assert len(texts) == 1
        assert texts[0].startswith("Ответить не получилось") and "аварийным рубильником" in texts[0]
        assert _stored("ans-gate") == texts
    finally:
        set_runtime(None)


class _LongActions(_FactActions):
    async def execute(self, name, params, task_ctx):
        out = await super().execute(name, params, task_ctx)
        if name == "logs.tail":
            body = {"text_ru": "ERROR api_key=sk-LIVEKEY1234567890abcd " + "строка " * 100}
            out.update(data=body, result=body)
        return out


@pytest.mark.asyncio
async def test_step_finished_carries_short_summary_and_message_has_task_id(token: str) -> None:
    del token
    rt = build_runtime(
        actions=_LongActions(fail={"diag.incidents"}),
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=FixedPlanner(_reads()),
    )
    try:
        task_id = await _run(rt, "sum-steps", "покажи ошибки из логов")
        events = rt.bus.replay("sum-steps", 0)
        finished = {
            event.payload["action"]: event.payload for event in events if event.type == "step_finished"
        }
        assert finished["project.status"]["summary"] == plain_ru("факт project.status")
        failed = finished["diag.incidents"]["summary"]
        assert failed.startswith("Не получилось «") and failed.endswith("журнал недоступен.")
        logs = finished["logs.tail"]["summary"]
        assert len(logs) <= 300 and logs.endswith("…") and "sk-LIVEKEY1234567890abcd" not in logs
        summary = next(event for event in events if event.type == "message")
        assert summary.task_id == task_id and summary.payload["task_id"] == task_id
    finally:
        set_runtime(None)


def test_step_summary_for_change_without_text() -> None:
    from app.orchestrator.agents.summarizer import step_summary

    note = StepNote("node.set_model", "Сменить модель ноды", ok=True, readonly=False)
    assert step_summary(note) == "Готово: Сменить модель ноды."
    assert step_summary(None) == ""


def test_deterministic_text_for_change_names_rollback() -> None:
    facts = TaskFacts(
        goal="поменяй модель",
        status="done",
        steps=[
            StepNote(
                "node.set_model",
                "Сменить модель ноды",
                ok=True,
                text="модель gpt-5.6-sol",
                readonly=False,
                undo={"action": "history.rollback", "snapshot_ids": [7]},
            ),
        ],
    )
    text = deterministic_text(facts)
    assert text.startswith("Готово. Что сделано:")
    assert "Сменить модель ноды: модель gpt-5.6-sol" in text
    # Подсказка — фраза, которую планёр понимает, без номеров снимков.
    assert "«верни как было»" in text and "снимк" not in text
    plain = deterministic_text(
        TaskFacts(
            goal="x", status="done", steps=[StepNote("node.set_param", "Параметр", ok=True, readonly=False)]
        )
    )
    assert "Снимка для автоматического отката" in plain


def test_explain_failure_is_plain_russian() -> None:
    blocked = PaidCallBlocked(BLOCK_KILL_SWITCH, "Платный вызов kie заблокирован аварийным рубильником.")
    assert explain_failure(blocked).startswith("Модель не вызвана: Платный вызов kie")
    assert explain_failure(ValueError("x")) == "Внутренняя ошибка: ValueError."


def test_catalog_lists_enum_values_and_step_names() -> None:
    from app.services.action_layer.spec import ensure_builtin_actions, registry

    ensure_builtin_actions()
    spec = registry.find("step.get_run_cost_basis")
    row = catalog_entry(spec)
    assert "img" in row["v"]["step_code"] and "images" not in row["v"]["step_code"]
    block = ActionCatalog(
        max_catalog=24, specs=lambda: [spec, registry.find("project.status")], project_of=lambda _t: None
    ).block("сколько стоит запуск шага картинок")
    assert '"step.get_run_cost_basis"' in block
    assert "img = Картинки" in block and "img_pr = Промты картинок" in block
    assert "«картинки» — step_code=img" in block
    status_only = ActionCatalog(
        max_catalog=24, specs=lambda: [registry.find("project.status")], project_of=lambda _t: None
    ).block("статус")
    assert "step_code" not in status_only
