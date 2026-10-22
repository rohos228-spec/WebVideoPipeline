"""Движок работает на одной базе, без адаптера «базы проекта».

Линия video-pipeline int/chat-v1 держала у проекта отдельный ``project.db`` и
открывала вторую сессию рядом с основной. В WebVideoPipeline база одна,
переходный адаптер ``app/project_db.py`` удалён: вызовы движка ходят прямо в
``app.db``. Тесты проверяют, что после слияния двух сессий в одну:

* чтения видят те же данные (кадры, проект, ноды);
* сверка восстановления (``reconcile``) принимает транзакционный ``node_change``
  единой базы (раньше требовался ``db_scope == "project"``, которого нет);
* ошибка продолжения откатывает только записи продолжения, а пометка
  ``human_review`` сохраняется — как было с двумя сессиями.
"""

from __future__ import annotations

import importlib.util
import re
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select

import app.db as app_db
from app.models import (
    Base,
    Frame,
    NodeRun,
    NodeRunStatus,
    Project,
    ProjectStatus,
    Workflow,
    WorkflowRun,
    WorkflowRunStatus,
)
from app.services import (  # noqa: F401  (модули движка регистрируют свои таблицы)
    actions_recovery,
    decision_config,
    decision_state,
    diagnosis,
    kill_switch,
    managed_launch,
    profiles_state,
    revisions,
)
from app.services.actions_recovery import RecoveryAction
from app.services.actions_v1 import ActionExecutionControl, ActionRecord
from app.services.observation import ExecutionControl, ObservationEvent, ObservationOutbox, Observer
from app.settings import settings

APP_DIR = Path(__file__).resolve().parents[1] / "app"


@pytest.fixture(autouse=True)
async def _engine_tables():
    # Таблицы движка (наблюдение, восстановление) живут в модулях сервисов;
    # шаблон схемы мог собраться до их импорта — догоняем create_all.
    async with app_db.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield


@pytest.fixture(autouse=True)
def _action_layer_sessions(monkeypatch):
    from app.services.action_layer import store

    monkeypatch.setattr(store, "_sessions", None)


async def _add_project(*, frames: int = 0, status: ProjectStatus = ProjectStatus.new, meta=None) -> int:
    async with app_db.session_scope() as s:
        project = Project(slug=f"single-{uuid.uuid4().hex[:8]}", topic="t", status=status, meta=meta or {})
        s.add(project)
        await s.flush()
        for i in range(frames, 0, -1):
            s.add(Frame(project_id=project.id, number=i, voiceover_text=f"реплика {i}", attrs={}))
        return project.id


# --- адаптера больше нет -----------------------------------------------------


def test_adapter_module_removed_and_not_imported():
    assert importlib.util.find_spec("app.project_db") is None
    offenders = []
    pattern = re.compile(r"\bapp\.project_db\b|from app import project_db")
    for path in APP_DIR.rglob("*.py"):
        if pattern.search(path.read_text(encoding="utf-8")):
            offenders.append(str(path.relative_to(APP_DIR)))
    assert offenders == []


# --- чтения через одну сессию ------------------------------------------------


async def test_project_frames_counts_single_db():
    from app.services.launch_inputs import plan_refusal, project_frames

    with_frames = await _add_project(frames=3)
    empty = await _add_project(frames=0)
    assert await project_frames(with_frames) == 3
    assert await project_frames(empty) == 0
    assert await project_frames(999_999) is None

    step = SimpleNamespace(action="step.run_step", params={"step_code": "img_pr"})
    assert await plan_refusal([step], empty) == (
        "Кадров нет — промты картинок делать не из чего, сначала разбивка на кадры (или импорт Excel).")
    assert await plan_refusal([step], with_frames) is None


async def test_status_sources_reports_one_frame_count():
    from app.services.action_layer import builtin_insight as bi

    pid = await _add_project(frames=2, status=ProjectStatus.paused)
    async with app_db.SessionLocal() as s:
        row = await s.get(Project, pid)
    src = await bi._status_sources(pid, row, ProjectStatus.plan_ready.value)
    assert src["mismatch"] is True
    assert src["frames_studio_db"] == 2
    assert "frames_project_db" not in src
    text = bi._mismatch_text(src)
    assert "Кадров в базе: 2." in text
    assert "базе проекта" not in text


async def test_snapshot_reads_single_sqlite_file():
    from app.orchestrator.validators import snapshot

    pid = await _add_project(frames=2)
    assert snapshot._resolve_path(pid, None) == Path(settings.sqlite_path)
    got = snapshot.load_project_snapshot(pid)
    assert got["available"] is True
    assert len(got["frames"]) == 2


def test_snapshot_on_postgres_reads_the_app_database(monkeypatch):
    """На Postgres файла нет: снимок читается соединением базы приложения
    (studio_read(None) ставит арендатора на каждую транзакцию), а не по пути."""
    from contextlib import contextmanager

    from app.orchestrator.validators import snapshot

    monkeypatch.setattr(type(settings), "is_postgres", property(lambda self: True))
    asked: list[object] = []

    @contextmanager
    def no_database(path):
        asked.append(path)
        yield None

    monkeypatch.setattr(snapshot, "studio_read", no_database)
    assert snapshot._resolve_path(1, None) is None
    assert snapshot.load_project_snapshot(1) == {"scenes": [], "frames": [], "available": False}
    assert asked == [None]


async def test_studio_allows_leaves_project_untouched():
    from app.services.action_layer.builtin_wave5 import _studio_allows

    step = SimpleNamespace(code="split", running_status=ProjectStatus.splitting)
    running = await _add_project(status=ProjectStatus.splitting)
    assert await _studio_allows(running, step) is False

    paused = await _add_project(status=ProjectStatus.paused,
                                meta={"paused_from_status": ProjectStatus.script_ready.value})
    await _studio_allows(paused, step, paused=True)
    async with app_db.SessionLocal() as s:
        project = await s.get(Project, paused)
    # Проверка подставляет статус до паузы только в памяти и откатывает сессию.
    assert project.status == ProjectStatus.paused


async def test_decision_reads_project_and_frames_from_one_session(tmp_path):
    from app.services.decision_v1 import _read_project_and_frames

    pid = await _add_project(frames=3)
    async with app_db.SessionLocal() as s:
        project, frames, files = await _read_project_and_frames(s, SimpleNamespace(project_id=pid))
        assert project.id == pid
        assert [f.number for f in frames] == [1, 2, 3]
        assert files == {"sfx_plan_file": False}
        Path(project.data_dir).mkdir(parents=True, exist_ok=True)
        (Path(project.data_dir) / "sfx_plan.json").write_text("{}", encoding="utf-8")
        _, _, files = await _read_project_and_frames(s, SimpleNamespace(project_id=pid))
        assert files == {"sfx_plan_file": True}
        with pytest.raises(ValueError, match="project contract unavailable"):
            await _read_project_and_frames(s, SimpleNamespace(project_id=999_999))


# --- сверка восстановления (reconcile) на одной сессии ---------------------


async def _seed_recovery(node_type: str, *, node_key: str = "n1") -> dict:
    execution_id = str(uuid.uuid4())
    attempt = str(uuid.uuid4())
    nodes = [{"id": node_key, "type": node_type, "data": {}}]
    graph = revisions.graph_version(nodes, [])
    old = datetime.utcnow() - timedelta(minutes=10)
    async with app_db.session_scope() as s:
        wf = Workflow(name=f"wf-{uuid.uuid4().hex[:8]}", is_default=False, nodes=[], edges=[])
        s.add(wf)
        await s.flush()
        project = Project(slug=f"rec-{uuid.uuid4().hex[:8]}", topic="t", status=ProjectStatus.paused, meta={})
        s.add(project)
        await s.flush()
        run = WorkflowRun(project_id=project.id, workflow_id=wf.id, status=WorkflowRunStatus.running,
                          nodes_snapshot=nodes, edges_snapshot=[])
        s.add(run)
        await s.flush()
        s.add(NodeRun(workflow_run_id=run.id, node_key=node_key, node_type=node_type,
                      status=NodeRunStatus.done))
        s.add(ExecutionControl(execution_id=execution_id, project_id=project.id, workflow_run_id=run.id,
                               node_keys=[node_key], graph_version=graph, mode="managed",
                               observer_state="active", cursors={}, gaps=[]))
        s.add(ActionExecutionControl(execution_id=execution_id, project_id=project.id,
                                     workflow_run_id=run.id, group_id="g", node_keys=[node_key],
                                     graph_version=graph, enabled=True, held=False,
                                     initiator="test", rationale="test"))
        action_id = str(uuid.uuid4())
        s.add(RecoveryAction(action_id=action_id, execution_id=execution_id, project_id=project.id,
                             incident_id=str(uuid.uuid4()), node_key=node_key,
                             source_attempt_id=str(uuid.uuid4()), new_attempt_id=attempt,
                             idempotency_key="k1", preconditions={}, budget_state={},
                             result={"worker_returned_at": old.isoformat()}, verification_result={},
                             status="await_verify", created_at=old))
        s.add(ActionRecord(action_id=action_id, execution_id=execution_id, project_id=project.id,
                           action_kind="node_recovery", node_key=node_key, attempt_id=attempt,
                           initiator="test", rationale="test", idempotency_key="k1",
                           status="queued", verification={}))
        facts = [
            ("node_start", {}),
            ("parse_result", {"payload_present": True}),
            ("data_applied", {}),
            # Единственный node_change одной базы — из flush-хука, db_scope="master".
            ("node_change", {"changes": {"status": {"before": ["running"], "after": ["done"]}},
                             "transactional": True}),
            ("node_end", {}),
        ]
        for kind, payload in facts:
            s.add(ObservationEvent(event_id=str(uuid.uuid4()), execution_id=execution_id,
                                   project_id=project.id, workflow_run_id=run.id, node_key=node_key,
                                   attempt_id=attempt, db_scope="master", kind=kind, payload=payload,
                                   created_at=old))
        return {"project_id": project.id, "action_id": action_id, "execution_id": execution_id}


def _observer(tmp_path) -> Observer:
    return Observer(app_db.SessionLocal, body_root=tmp_path / "bodies")


async def _state(seed):
    async with app_db.SessionLocal() as s:
        row = await s.get(RecoveryAction, seed["action_id"])
        record = await s.get(ActionRecord, seed["action_id"])
        control = await s.get(ActionExecutionControl, seed["execution_id"])
        project = await s.get(Project, seed["project_id"])
        outbox = (await s.scalars(select(ObservationOutbox).where(
            ObservationOutbox.execution_id == seed["execution_id"]))).all()
        return row, record, control, project, outbox


async def test_reconcile_verifies_on_single_db_node_change(tmp_path):
    seed = await _seed_recovery("plan")
    await actions_recovery.reconcile(_observer(tmp_path))
    row, record, control, _project, outbox = await _state(seed)
    assert row.verification_result["transactional_done"] is True
    assert row.verification_result["graph_matches"] is True
    assert row.status == "verified"
    assert record.status == "verified"
    assert control.held is False
    assert row.result["continuation_submitted"] is False
    assert [o.kind for o in outbox] == ["recovery_verification"]


async def test_reconcile_continuation_commits_with_ticket(tmp_path, monkeypatch):
    seed = await _seed_recovery("excel_gpt")

    async def _continue(session, project, *, node_key, slot_idx, ready_status):
        project.meta = {**(project.meta or {}), "active_excel_gpt_node_key": "n2"}
        await session.flush()

    monkeypatch.setattr("app.orchestrator.steps.enrich_xlsx._after_excel_gpt_done", _continue)
    await actions_recovery.reconcile(_observer(tmp_path))
    row, _record, _control, project, outbox = await _state(seed)
    assert row.status == "verified"
    assert row.result["continuation_submitted"] is True
    assert project.meta["active_excel_gpt_node_key"] == "n2"
    submitted = [o for o in outbox if o.kind == "recovery_continuation_submitted"]
    assert len(submitted) == 1
    # Событие продолжения пишется в ту же базу и читается обычным consume("master").
    assert submitted[0].db_scope == "master"
    assert submitted[0].payload["next_node_key"] == "n2"


async def test_reconcile_continuation_failure_rolls_back_only_continuation(tmp_path, monkeypatch):
    seed = await _seed_recovery("excel_gpt")

    async def _broken(session, project, *, node_key, slot_idx, ready_status):
        project.meta = {**(project.meta or {}), "half_written": True}
        await session.flush()
        raise RuntimeError("continuation failed")

    monkeypatch.setattr("app.orchestrator.steps.enrich_xlsx._after_excel_gpt_done", _broken)
    with pytest.raises(RuntimeError, match="continuation failed"):
        await actions_recovery.reconcile(_observer(tmp_path))
    row, record, control, project, outbox = await _state(seed)
    assert "half_written" not in (project.meta or {})
    assert row.status == "human_review"
    assert record.status == "human_review"
    assert row.result["continuation_unconfirmed"] is True
    assert row.result["continuation_error_type"] == "RuntimeError"
    assert control.held is True
    assert not [o for o in outbox if o.kind == "recovery_continuation_submitted"]


# --- выдача билета восстановления (recover_node) на одной сессии ------------


async def _seed_recover_ticket(monkeypatch, tmp_path, *, node_key: str = "n_plan") -> dict:
    from app.services import observation
    from app.services.diagnosis import DiagnosisState, Incident

    monkeypatch.delenv("ORCHESTRATOR_RUNTIME_CONFIG", raising=False)
    monkeypatch.setattr(observation, "_observer", None)
    # Тело исходного запроса лежит в хранилище наблюдения; здесь важна не
    # нормализация тела, а то, что билет и проект пишутся одной транзакцией.
    monkeypatch.setattr(actions_recovery, "load_source_body", lambda observer, start: {"input": "x"})
    execution_id = str(uuid.uuid4())
    attempt = str(uuid.uuid4())
    nodes = [{"id": node_key, "type": "plan", "data": {}}]
    graph = revisions.graph_version(nodes, [])
    meta = {"step_failure": {"abandoned_at": datetime.utcnow().isoformat(), "abandoned_reason": "exhausted",
                             "abandoned_threshold": 2, "last_running": ProjectStatus.planning.value,
                             "total_fails": {ProjectStatus.planning.value: 3}}}
    async with app_db.session_scope() as s:
        wf = Workflow(name=f"wf-{uuid.uuid4().hex[:8]}", is_default=False, nodes=[], edges=[])
        s.add(wf)
        await s.flush()
        project = Project(slug=f"tic-{uuid.uuid4().hex[:8]}", topic="t", status=ProjectStatus.paused, meta=meta)
        s.add(project)
        await s.flush()
        run = WorkflowRun(project_id=project.id, workflow_id=wf.id, status=WorkflowRunStatus.running,
                          nodes_snapshot=nodes, edges_snapshot=[])
        s.add(run)
        await s.flush()
        s.add(NodeRun(workflow_run_id=run.id, node_key=node_key, node_type="plan",
                      status=NodeRunStatus.failed, finished_at=datetime.utcnow()))
        s.add(ExecutionControl(execution_id=execution_id, project_id=project.id, workflow_run_id=run.id,
                               node_keys=[node_key], graph_version=graph, mode="managed",
                               observer_state="active", cursors={}, gaps=[]))
        s.add(ActionExecutionControl(execution_id=execution_id, project_id=project.id,
                                     workflow_run_id=run.id, group_id="g", node_keys=[node_key],
                                     graph_version=graph, enabled=True, held=False,
                                     initiator="test", rationale="test"))
        await s.flush()
        parts: dict = {}
        revision = await actions_recovery.input_revision(s, project, node_key, parts=parts)
        request_id = str(uuid.uuid4())
        facts = [
            ("node_start", None, {"recovery_input_revision": revision, "recovery_revision_parts": parts}),
            ("model_start", request_id, {"input_revision": "body-hash"}),
            ("model_end", request_id, {"complete": True}),
            ("node_error", None, {}),
        ]
        events = []
        for kind, rid, payload in facts:
            event = ObservationEvent(event_id=str(uuid.uuid4()), execution_id=execution_id,
                                     project_id=project.id, workflow_run_id=run.id, node_key=node_key,
                                     attempt_id=attempt, request_id=rid, db_scope="master", kind=kind,
                                     payload=payload, created_at=datetime.utcnow())
            s.add(event)
            events.append(event)
        await s.flush()
        incident_id = str(uuid.uuid4())
        s.add(Incident(incident_id=incident_id, execution_id=execution_id, project_id=project.id,
                       node_key=node_key, attempt_id=attempt, type="invalid_json", severity="error",
                       status="open", details={"currently_relevant": True, "event_ids": []}))
        s.add(DiagnosisState(execution_id=execution_id, cursor=events[-1].id))
        project_id = project.id
    return {"project_id": project_id, "execution_id": execution_id, "incident_id": incident_id,
            "node_key": node_key}


async def _recover(seed, key="rk-1"):
    from app.services.actions_v1 import ActionSupervisor

    return await ActionSupervisor(app_db.SessionLocal).recover_node(
        project_id=seed["project_id"], execution_id=seed["execution_id"], incident_id=seed["incident_id"],
        node_key=seed["node_key"], idempotency_key=key, initiator="test", rationale="retry once")


async def _ticket_state(seed):
    async with app_db.SessionLocal() as s:
        project = await s.get(Project, seed["project_id"])
        node = await s.scalar(select(NodeRun).where(NodeRun.node_key == seed["node_key"]))
        action = await s.scalar(select(RecoveryAction).where(
            RecoveryAction.execution_id == seed["execution_id"]))
        control = await s.get(ActionExecutionControl, seed["execution_id"])
        return project, node, action, control


async def test_recover_node_dispatch_commits_ticket_project_and_node(monkeypatch, tmp_path):
    seed = await _seed_recover_ticket(monkeypatch, tmp_path)
    ticket = await _recover(seed)
    assert ticket["idempotent_replay"] is False
    project, node, action, control = await _ticket_state(seed)
    assert action.status == "queued"
    assert action.preconditions["workflow_aggregate_status"] == WorkflowRunStatus.running.value
    assert "node_and_project_replicas_match" not in action.preconditions
    assert project.status == ProjectStatus.planning
    assert node.status == NodeRunStatus.running
    assert control.held is False
    actions_recovery.clear_restriction(seed["project_id"])


async def test_recover_node_fsm_refusal_keeps_node_failed(monkeypatch, tmp_path):
    seed = await _seed_recover_ticket(monkeypatch, tmp_path)

    def _refuse(nr, *, project_id, initiator="api"):
        nr.status = NodeRunStatus.queued  # FSM успела поставить в очередь и отказала
        return False

    monkeypatch.setattr("app.services.node_status_machine.start_node_running", _refuse)
    with pytest.raises(ValueError, match="FSM refused recovery dispatch"):
        await _recover(seed)
    project, node, action, control = await _ticket_state(seed)
    assert action.status == "human_review"
    assert action.result["dispatch_refused"] is True
    assert control.held is True
    # Несохранённая «queued» не утекла в commit вместе с билетом.
    assert node.status == NodeRunStatus.failed
    assert project.status == ProjectStatus.paused
    actions_recovery.clear_restriction(seed["project_id"])


async def test_recover_node_refuses_user_stop_without_ticket(monkeypatch, tmp_path):
    seed = await _seed_recover_ticket(monkeypatch, tmp_path)
    async with app_db.session_scope() as s:
        project = await s.get(Project, seed["project_id"])
        project.meta = {**project.meta, "user_stop": True}
    with pytest.raises(ValueError, match="user_stop is active"):
        await _recover(seed)
    _project, node, action, _control = await _ticket_state(seed)
    assert action is None
    assert node.status == NodeRunStatus.failed
