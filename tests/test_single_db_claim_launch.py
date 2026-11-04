"""Управляемый запуск и билет восстановления на одной базе.

Продолжение ``test_single_db_engine_sessions``: после удаления адаптера базы
проекта у ``managed_launch.prepare`` нет второй сессии и сверки «основная
против копии проекта», а ``actions_recovery.claim`` читает и пишет билет одной
сессией наблюдателя. Проверяется:

* проверки занятости нод не падают на отсутствующем в модели статусе
  ``waiting_approval`` (раньше — AttributeError на любом непустом списке);
* запуск готовит строки одной сессией и не зависит от «копии» проекта;
* билет забирается одной сессией, и откат попытки воркера его не отменяет;
* если воркер держит незакоммиченную запись, забор отказывает сразу, а не
  ждёт минуту блокировки SQLite.
"""

from __future__ import annotations

import inspect
import time
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import select

import app.db as app_db
from app.models import (
    Base,
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
    actions_v1,
    decision_config,
    decision_state,
    diagnosis,
    kill_switch,
    managed_launch,
    media_jobs,
    profiles_state,
    revisions,
)
from app.services.actions_recovery import RecoveryAction
from app.services.actions_v1 import (
    ACTIVE_NODE_STATUSES,
    BUSY_NODE_STATUSES,
    ActionExecutionControl,
    ActionRecord,
)
from app.services.observation import ExecutionControl, Observer


@pytest.fixture(autouse=True)
async def _engine_tables():
    async with app_db.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield


def _observer(tmp_path) -> Observer:
    return Observer(app_db.SessionLocal, body_root=tmp_path / "bodies")


async def _seed_run(*, node_status=NodeRunStatus.pending, node_key="n_plan", project_meta=None) -> dict:
    nodes = [{"id": node_key, "type": "plan", "data": {}}]
    async with app_db.session_scope() as s:
        wf = Workflow(name=f"wf-{uuid.uuid4().hex[:8]}", is_default=False, nodes=[], edges=[])
        s.add(wf)
        await s.flush()
        project = Project(
            slug=f"ml-{uuid.uuid4().hex[:8]}", topic="t", status=ProjectStatus.new, meta=project_meta or {}
        )
        s.add(project)
        await s.flush()
        run = WorkflowRun(
            project_id=project.id,
            workflow_id=wf.id,
            status=WorkflowRunStatus.running,
            nodes_snapshot=nodes,
            edges_snapshot=[],
        )
        s.add(run)
        await s.flush()
        s.add(NodeRun(workflow_run_id=run.id, node_key=node_key, node_type="plan", status=node_status))
        return {
            "project_id": project.id,
            "run_id": run.id,
            "node_key": node_key,
            "graph": revisions.graph_version(nodes, []),
        }


# --- статусы занятости ------------------------------------------------------


def test_busy_status_sets_hold_only_model_members():
    members = set(NodeRunStatus)
    assert members >= ACTIVE_NODE_STATUSES and members >= BUSY_NODE_STATUSES
    assert {NodeRunStatus.running, NodeRunStatus.queued, NodeRunStatus.waiting_hitl} == ACTIVE_NODE_STATUSES
    assert {NodeRunStatus.running, NodeRunStatus.queued} == BUSY_NODE_STATUSES
    # Старое имя из actions_recovery остаётся тем же множеством.
    assert actions_recovery.ACTIVE_NODE_STATUSES is ACTIVE_NODE_STATUSES


@pytest.mark.parametrize("status", [NodeRunStatus.waiting_hitl, NodeRunStatus.running, NodeRunStatus.queued])
def test_validate_scope_refuses_active_node_without_attribute_error(status):
    nodes = [{"id": "n_plan", "type": "plan", "data": {}}]
    run = SimpleNamespace(nodes_snapshot=nodes, edges_snapshot=[])
    project = SimpleNamespace(id=1, meta={})
    rows = [SimpleNamespace(node_key="n_plan", status=status, attempts=0)]
    with pytest.raises(ValueError, match="active node or HITL must settle first"):
        managed_launch.validate_scope(run, project, rows, ["n_plan"])


def test_validate_scope_accepts_fresh_pending_node():
    nodes = [{"id": "n_plan", "type": "plan", "data": {}}]
    run = SimpleNamespace(nodes_snapshot=nodes, edges_snapshot=[])
    project = SimpleNamespace(id=1, meta={})
    rows = [SimpleNamespace(node_key="n_plan", status=NodeRunStatus.pending, attempts=0)]
    entry, contracts = managed_launch.validate_scope(run, project, rows, ["n_plan"])
    assert entry == "n_plan"
    assert "retry_same" in contracts["n_plan"]["actions"]


async def _seed_group(node_status, *, mode="observe") -> dict:
    seed = await _seed_run(node_status=node_status)
    nodes = [{"id": seed["node_key"], "type": "plan", "data": {"groupId": "grp"}}]
    graph = revisions.graph_version(nodes, [])
    execution_id = str(uuid.uuid4())
    async with app_db.session_scope() as s:
        run = await s.get(WorkflowRun, seed["run_id"])
        run.nodes_snapshot = nodes
        s.add(
            ExecutionControl(
                execution_id=execution_id,
                project_id=seed["project_id"],
                workflow_run_id=seed["run_id"],
                node_keys=[seed["node_key"]],
                graph_version=graph,
                mode=mode,
                observer_state="active",
                cursors={},
                gaps=[],
            )
        )
    return {**seed, "graph": graph, "execution_id": execution_id}


async def _enable(seed):
    return await actions_v1.ActionSupervisor(app_db.SessionLocal).enable(
        project_id=seed["project_id"],
        execution_id=seed["execution_id"],
        group_id="grp",
        node_keys=[seed["node_key"]],
        graph_version=seed["graph"],
        initiator="test",
        rationale="группа",
        request_limit=3,
    )


@pytest.mark.parametrize("status", [NodeRunStatus.queued, NodeRunStatus.running])
async def test_enable_refuses_busy_group_without_attribute_error(status):
    seed = await _seed_group(status)
    with pytest.raises(ValueError, match="already has queued/running nodes"):
        await _enable(seed)


async def test_enable_then_close_idle_group():
    seed = await _seed_group(NodeRunStatus.pending)
    supervisor = actions_v1.ActionSupervisor(app_db.SessionLocal)
    try:
        result = await _enable(seed)
        assert result["enabled"] is True
        closed = await supervisor.close(
            project_id=seed["project_id"],
            execution_id=seed["execution_id"],
            initiator="test",
            rationale="хватит",
            idempotency_key="close-1",
        )
        assert closed["enabled"] is False
    finally:
        actions_v1.clear_permission(seed["project_id"])


async def test_close_refuses_while_node_queued():
    seed = await _seed_group(NodeRunStatus.pending)
    supervisor = actions_v1.ActionSupervisor(app_db.SessionLocal)
    try:
        await _enable(seed)
        async with app_db.session_scope() as s:
            node = await s.scalar(select(NodeRun).where(NodeRun.workflow_run_id == seed["run_id"]))
            node.status = NodeRunStatus.queued
        with pytest.raises(ValueError, match="cannot revoke while a managed node is queued/running"):
            await supervisor.close(
                project_id=seed["project_id"],
                execution_id=seed["execution_id"],
                initiator="test",
                rationale="хватит",
                idempotency_key="close-1",
            )
    finally:
        actions_v1.clear_permission(seed["project_id"])


# --- управляемый запуск одной сессией --------------------------------------


def test_prepare_has_no_project_session_argument():
    params = inspect.signature(managed_launch.prepare).parameters
    assert "project_session" not in params
    assert list(params)[:1] == ["observer"]


def _request(seed, **over):
    return {
        "idempotency_key": "launch-1",
        "workflow_run_id": seed["run_id"],
        "graph_version": seed["graph"],
        "node_keys": [seed["node_key"]],
        "strategies": ["retry_same"],
        "request_limit": 3,
        "rationale": "один шаг",
        **over,
    }


@pytest.fixture
def _launch_config(monkeypatch):
    cfg = {
        "version": 1,
        "mode": "synthetic",
        "online_enabled": True,
        "endpoint": "http://127.0.0.1/x",
        "decision": {"catalog_id": "gpt-kie"},
        "profiles": {},
        "allowed_actions": ["retry_same", "read_events", "read_contract", "finish"],
    }
    monkeypatch.setattr(decision_config, "runtime_config", lambda: cfg)
    monkeypatch.setattr(decision_config, "check_route", lambda config, url: None)
    return cfg


async def test_prepare_creates_launch_from_one_session(tmp_path, _launch_config):
    seed = await _seed_run()
    observer = _observer(tmp_path)
    try:
        row, replay = await managed_launch.prepare(
            observer, project_id=seed["project_id"], request=_request(seed), initiator="test"
        )
        assert replay is False
        assert row.entry_node == seed["node_key"]
        assert row.status == "dispatching"
        assert "initial_status_projection" not in row.policy
        async with app_db.SessionLocal() as s:
            stored = await s.get(managed_launch.ManagedLaunch, row.launch_id)
            control = await s.get(ActionExecutionControl, row.execution_id)
            project = await s.get(Project, seed["project_id"])
        assert stored is not None and stored.workflow_run_id == seed["run_id"]
        assert control.enabled is True and control.held is False
        # Статус проекта запуск не трогает: проецировать «из базы проекта» нечего.
        assert project.status == ProjectStatus.new
        again, replay = await managed_launch.prepare(
            observer, project_id=seed["project_id"], request=_request(seed), initiator="test"
        )
        assert replay is True and again.launch_id == row.launch_id
    finally:
        actions_v1.clear_permission(seed["project_id"])


async def test_prepare_refuses_active_node(tmp_path, _launch_config):
    seed = await _seed_run(node_status=NodeRunStatus.waiting_hitl)
    with pytest.raises(ValueError, match="active node or HITL must settle first"):
        await managed_launch.prepare(
            _observer(tmp_path), project_id=seed["project_id"], request=_request(seed), initiator="test"
        )
    async with app_db.SessionLocal() as s:
        assert (await s.scalars(select(managed_launch.ManagedLaunch))).all() == []


async def test_prepare_refuses_user_stop(tmp_path, _launch_config):
    seed = await _seed_run(project_meta={"user_stop": True})
    with pytest.raises(ValueError, match="user_stop is active"):
        await managed_launch.prepare(
            _observer(tmp_path), project_id=seed["project_id"], request=_request(seed), initiator="test"
        )


async def test_prepare_refuses_changed_graph(tmp_path, _launch_config):
    seed = await _seed_run()
    with pytest.raises(ValueError, match="graph revision changed"):
        await managed_launch.prepare(
            _observer(tmp_path),
            project_id=seed["project_id"],
            request=_request(seed, graph_version="other"),
            initiator="test",
        )


# --- забор билета восстановления одной сессией -----------------------------


async def _seed_ticket(seed, *, graph_version=None) -> dict:
    execution_id = str(uuid.uuid4())
    async with app_db.session_scope() as s:
        project = await s.get(Project, seed["project_id"])
        revision = await actions_recovery.input_revision(s, project, seed["node_key"])
        s.add(
            ActionExecutionControl(
                execution_id=execution_id,
                project_id=seed["project_id"],
                workflow_run_id=seed["run_id"],
                group_id="g",
                node_keys=[seed["node_key"]],
                graph_version=seed["graph"],
                enabled=True,
                held=False,
                initiator="test",
                rationale="test",
            )
        )
        action_id = str(uuid.uuid4())
        new_attempt = str(uuid.uuid4())
        s.add(
            RecoveryAction(
                action_id=action_id,
                execution_id=execution_id,
                project_id=seed["project_id"],
                incident_id=str(uuid.uuid4()),
                node_key=seed["node_key"],
                source_attempt_id=str(uuid.uuid4()),
                new_attempt_id=new_attempt,
                idempotency_key="k1",
                preconditions={
                    "graph_version": graph_version or seed["graph"],
                    "input_revision": revision,
                    "action_type": "retry_same",
                },
                budget_state={},
                result={},
                verification_result={},
                status="queued",
            )
        )
        s.add(
            ActionRecord(
                action_id=action_id,
                execution_id=execution_id,
                project_id=seed["project_id"],
                action_kind="node_recovery",
                node_key=seed["node_key"],
                attempt_id=new_attempt,
                initiator="test",
                rationale="test",
                idempotency_key="k1",
                status="queued",
                verification={},
            )
        )
    return {"execution_id": execution_id, "action_id": action_id, "new_attempt": new_attempt}


async def _ticket(ticket):
    async with app_db.SessionLocal() as s:
        return (
            await s.get(RecoveryAction, ticket["action_id"]),
            await s.get(ActionRecord, ticket["action_id"]),
        )


async def test_claim_survives_worker_rollback(tmp_path):
    seed = await _seed_run(node_status=NodeRunStatus.failed)
    ticket = await _seed_ticket(seed)
    try:
        async with app_db.SessionLocal() as worker:
            project = await worker.get(Project, seed["project_id"])
            got = await actions_recovery.claim(
                _observer(tmp_path),
                worker,
                project,
                seed["node_key"],
                "fresh-attempt",
                execution_id=ticket["execution_id"],
            )
            assert got == ticket["new_attempt"]
            project.meta = {**(project.meta or {}), "worker_wrote": True}
            await worker.flush()
            await worker.rollback()
        row, record = await _ticket(ticket)
        assert row.status == "claimed" and row.result["worker_claimed"] is True
        assert record.status == "running"
        async with app_db.SessionLocal() as s:
            assert "worker_wrote" not in ((await s.get(Project, seed["project_id"])).meta or {})
    finally:
        actions_recovery.finish_job_tracking(seed["project_id"])


async def test_claim_without_ticket_keeps_attempt_id(tmp_path):
    seed = await _seed_run(node_status=NodeRunStatus.failed)
    async with app_db.SessionLocal() as worker:
        project = await worker.get(Project, seed["project_id"])
        got = await actions_recovery.claim(
            _observer(tmp_path),
            worker,
            project,
            seed["node_key"],
            "fresh-attempt",
            execution_id=str(uuid.uuid4()),
        )
    assert got == "fresh-attempt"


async def test_claim_refuses_changed_graph(tmp_path):
    seed = await _seed_run(node_status=NodeRunStatus.failed)
    ticket = await _seed_ticket(seed, graph_version="stale")
    async with app_db.SessionLocal() as worker:
        project = await worker.get(Project, seed["project_id"])
        with pytest.raises(RuntimeError, match="revisions changed before claim"):
            await actions_recovery.claim(
                _observer(tmp_path),
                worker,
                project,
                seed["node_key"],
                "fresh-attempt",
                execution_id=ticket["execution_id"],
            )
    row, _record = await _ticket(ticket)
    assert row.status == "queued"


async def test_claim_refuses_fast_when_worker_holds_write(tmp_path):
    seed = await _seed_run(node_status=NodeRunStatus.failed)
    ticket = await _seed_ticket(seed)
    async with app_db.SessionLocal() as worker:
        project = await worker.get(Project, seed["project_id"])
        project.meta = {**(project.meta or {}), "pending": True}
        await worker.flush()  # запись ушла в SQLite, транзакция открыта
        started = time.monotonic()
        with pytest.raises(RuntimeError, match="without pending writes"):
            await actions_recovery.claim(
                _observer(tmp_path),
                worker,
                project,
                seed["node_key"],
                "fresh-attempt",
                execution_id=ticket["execution_id"],
            )
        # Отказ сразу, без ожидания busy_timeout второго писателя.
        assert time.monotonic() - started < 5
        await worker.rollback()
    row, record = await _ticket(ticket)
    assert row.status == "queued" and record.status == "queued"


async def test_claim_reads_only_committed_state_after_worker_reads(tmp_path):
    """Чтения воркера транзакцию в драйвере не открывают — забор идёт без ожидания."""
    seed = await _seed_run(node_status=NodeRunStatus.failed)
    ticket = await _seed_ticket(seed)
    try:
        async with app_db.SessionLocal() as worker:
            project = await worker.get(Project, seed["project_id"])
            await worker.scalars(select(NodeRun).where(NodeRun.workflow_run_id == seed["run_id"]))
            assert await actions_recovery._holds_sqlite_write(worker) is False
            started = time.monotonic()
            await actions_recovery.claim(
                _observer(tmp_path),
                worker,
                project,
                seed["node_key"],
                "fresh-attempt",
                execution_id=ticket["execution_id"],
            )
            assert time.monotonic() - started < 5
        row, _record = await _ticket(ticket)
        assert row.status == "claimed"
    finally:
        actions_recovery.finish_job_tracking(seed["project_id"])
