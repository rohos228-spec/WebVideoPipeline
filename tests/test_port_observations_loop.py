"""Цикл наблюдения и сторож пульса в lifespan (перенос int/chat-v1 bf984a81b).

Главное условие владельца: по умолчанию ничто в фоне не включает ГЛОБАЛЬНЫЙ
аварийный стоп. Зависание даёт инцидент «stalled», запись в журнал действий и
уведомление в чат — но не стоп всего сервера. Стоп от сторожа — только при явном
``WATCHDOG_KILL_SWITCH_ON_STALL=true``.

Ещё: цикл поднимается по ENGINE_STARTUP и гасится на остановке; шаг решений без
конфигурации модели не может сделать ни одного платного вызова.

База — временный SQLite из conftest; живой сервер не трогается.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from app.services import observation as observation_mod
from app.services.observation import ExecutionControl, ObservationEvent, Observer, observation_tables

observation_tables()

from app.models import (  # noqa: E402
    NodeRun,
    NodeRunStatus,
    Project,
    ProjectStatus,
    Workflow,
    WorkflowRun,
)
from app.settings import Settings, settings  # noqa: E402


@pytest.fixture
def heartbeat_dir(tmp_path, monkeypatch):
    d = tmp_path / "hb"
    monkeypatch.setenv("VP_HEARTBEAT_DIR", str(d))
    return d


async def _observer(tmp_path, monkeypatch) -> Observer:
    import app.db as app_db
    from app.services import kill_switch
    from app.services.observation import migrate_observation_schema

    async with app_db.engine.begin() as conn:
        await migrate_observation_schema(conn)
    kill_switch.reset_cache()
    observer = Observer(app_db.SessionLocal, body_root=tmp_path / "bodies")
    monkeypatch.setattr(observation_mod, "_observer", observer)
    return observer


async def _stalled_execution(observer: Observer) -> str:
    """Активное наблюдение: запрос модели начат 2 часа назад и не завершён, нода running."""
    execution = str(uuid.uuid4())
    old = datetime.utcnow() - timedelta(hours=2)
    async with observer.master() as s:
        p = Project(topic="stall", slug=f"stall-{execution[:8]}", status=ProjectStatus.new)
        s.add(p)
        await s.flush()
        wf = Workflow(name="wf", nodes=[{"id": "n1", "type": "excel_gpt"}], edges=[])
        s.add(wf)
        await s.flush()
        run = WorkflowRun(
            workflow_id=wf.id,
            project_id=p.id,
            nodes_snapshot=[{"id": "n1", "type": "excel_gpt"}],
            edges_snapshot=[],
        )
        s.add(run)
        await s.flush()
        s.add(
            NodeRun(
                workflow_run_id=run.id,
                node_key="n1",
                node_type="excel_gpt",
                status=NodeRunStatus.running,
                started_at=old,
            )
        )
        s.add(
            ExecutionControl(
                execution_id=execution,
                project_id=p.id,
                workflow_run_id=run.id,
                node_keys=["n1"],
                graph_version="g" * 64,
                mode="observe",
                observer_state="active",
                cursors={},
                gaps=[],
            )
        )
        attempt = str(uuid.uuid4())
        for kind, request in (("node_start", None), ("model_start", str(uuid.uuid4()))):
            s.add(
                ObservationEvent(
                    event_id=str(uuid.uuid4()),
                    execution_id=execution,
                    project_id=p.id,
                    workflow_run_id=run.id,
                    node_key="n1",
                    attempt_id=attempt,
                    request_id=request,
                    db_scope="master",
                    kind=kind,
                    payload={},
                    created_at=old,
                )
            )
        await s.commit()
    return execution


def _capture_notices(monkeypatch) -> list[tuple[str, str]]:
    from app.services import supervisor_heartbeat

    sent: list[tuple[str, str]] = []

    async def _send(text, kind, project_id=None):
        sent.append((kind, text))

    monkeypatch.setattr(supervisor_heartbeat, "_send", _send)
    return sent


async def _stalled_incidents(observer: Observer, execution: str) -> list:
    from app.services.diagnosis import Incident

    async with observer.master() as s:
        return (
            await s.scalars(
                select(Incident).where(Incident.execution_id == execution, Incident.type == "stalled")
            )
        ).all()


async def _kill_active(observer: Observer) -> bool:
    from app.services import kill_switch

    async with observer.master() as s:
        return await kill_switch.is_active(s)


# ── значения по умолчанию ────────────────────────────────────────────────────


def test_no_automatic_kill_switch_by_default(monkeypatch) -> None:
    monkeypatch.delenv("WATCHDOG_KILL_SWITCH_ON_STALL", raising=False)
    assert Settings.model_fields["watchdog_kill_switch_on_stall"].default is False
    assert Settings(_env_file=None).watchdog_kill_switch_on_stall is False
    # 402 провайдера → стоп: поля в настройках нет вовсе, getattr(..., False) всегда False.
    assert "spend_auto_kill_on_402" not in Settings.model_fields
    assert getattr(settings, "spend_auto_kill_on_402", False) is False


def test_decision_model_is_off_without_owner_config(monkeypatch) -> None:
    """Шаг решений в цикле без ORCHESTRATOR_RUNTIME_CONFIG не может звать модель."""
    from app.services import decision_config

    monkeypatch.delenv("ORCHESTRATOR_RUNTIME_CONFIG", raising=False)
    monkeypatch.setattr(settings, "gpt_base_url", "https://api.example.invalid/v1")
    cfg = decision_config.runtime_config()
    assert cfg["online_enabled"] is False and cfg["decision"] is None and cfg["allowed_actions"] == []
    assert decision_config.status()["configured"] is False
    with pytest.raises(ValueError):
        decision_config.check_route(cfg, "https://api.example.invalid/v1")


# ── сторож зависаний ─────────────────────────────────────────────────────────


async def test_stall_gives_incident_and_notice_not_a_server_stop(tmp_path, monkeypatch) -> None:
    from app.services.actions_v1 import ActionRecord

    monkeypatch.setattr(settings, "watchdog_enabled", True)
    monkeypatch.setattr(settings, "watchdog_kill_switch_on_stall", False)
    observer = await _observer(tmp_path, monkeypatch)
    sent = _capture_notices(monkeypatch)
    execution = await _stalled_execution(observer)

    await observer._watchdog()

    incidents = await _stalled_incidents(observer, execution)
    assert incidents and incidents[0].status == "human_review"
    async with observer.master() as s:
        records = (
            await s.scalars(
                select(ActionRecord).where(
                    ActionRecord.execution_id == execution, ActionRecord.action_kind == "watchdog_stalled"
                )
            )
        ).all()
    assert records and all(r.verification.get("business_action") is False for r in records)
    assert [k for k, _ in sent] and {k for k, _ in sent} == {"watchdog_stalled"}
    assert await _kill_active(observer) is False


async def test_kill_switch_on_stall_only_when_explicitly_enabled(tmp_path, monkeypatch) -> None:
    """Обратная проверка: стоп даёт ровно этот флаг, значит без него стопа нет."""
    monkeypatch.setattr(settings, "watchdog_enabled", True)
    monkeypatch.setattr(settings, "watchdog_kill_switch_on_stall", True)
    observer = await _observer(tmp_path, monkeypatch)
    _capture_notices(monkeypatch)
    await _stalled_execution(observer)

    await observer._watchdog()

    assert await _kill_active(observer) is True
    from app.services import kill_switch

    state = await kill_switch.status(observer.master)
    assert state["source"] == "watchdog" and state["changed_by"] == "watchdog"
    await kill_switch.set_state(observer.master, active=False, actor="test", rationale="снять после теста")


async def test_real_loop_with_defaults_never_engages_the_kill_switch(
    tmp_path, monkeypatch, heartbeat_dir
) -> None:
    """Настоящий ``Observer.loop`` с настройками по умолчанию на зависшем выполнении."""
    from app.services import supervisor_heartbeat

    monkeypatch.delenv("ORCHESTRATOR_RUNTIME_CONFIG", raising=False)
    monkeypatch.setattr(settings, "watchdog_enabled", Settings.model_fields["watchdog_enabled"].default)
    monkeypatch.setattr(
        settings,
        "watchdog_kill_switch_on_stall",
        Settings.model_fields["watchdog_kill_switch_on_stall"].default,
    )
    observer = await _observer(tmp_path, monkeypatch)
    _capture_notices(monkeypatch)
    execution = await _stalled_execution(observer)

    task = asyncio.create_task(observer.loop())
    try:
        for _ in range(100):
            await asyncio.sleep(0.1)
            if await _stalled_incidents(observer, execution):
                break
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert await _stalled_incidents(observer, execution), observer.stage_errors
    assert await _kill_active(observer) is False
    hb = supervisor_heartbeat.read_heartbeat(heartbeat_dir)
    assert hb is not None and hb["stopped"] is False


# ── lifespan ─────────────────────────────────────────────────────────────────


class _FakeObserver:
    def __init__(self) -> None:
        self.started = 0

    async def loop(self) -> None:
        self.started += 1
        await asyncio.sleep(3600)


async def test_start_observer_starts_loop_and_checker_and_stops_cleanly(monkeypatch, heartbeat_dir) -> None:
    from app.services import supervisor_heartbeat
    from app.web import api

    monkeypatch.setattr(settings, "engine_startup", True)
    monkeypatch.delenv("VP_SUPERVISOR_HEARTBEAT", raising=False)
    fake = _FakeObserver()
    monkeypatch.setattr(observation_mod, "get_observer", lambda: fake)
    checks: list[int] = []

    async def _checker():
        checks.append(1)
        await asyncio.sleep(3600)

    monkeypatch.setattr(supervisor_heartbeat, "run_checker", _checker)

    tasks = api._start_observer()
    assert [t.get_name() for t in tasks] == ["observation-consumer", "observer-heartbeat-checker"]
    await asyncio.sleep(0)
    assert fake.started == 1 and checks == [1]

    await api._stop_observer(tasks)
    assert all(t.cancelled() for t in tasks)
    hb = supervisor_heartbeat.read_heartbeat(heartbeat_dir)
    assert hb is not None and hb["stopped"] is True


async def test_start_observer_respects_engine_startup_off(monkeypatch) -> None:
    from app.web import api

    monkeypatch.setattr(settings, "engine_startup", False)

    def _boom():
        raise AssertionError("цикл наблюдения поднят при ENGINE_STARTUP=0")

    monkeypatch.setattr(observation_mod, "get_observer", _boom)
    assert api._start_observer() == []
    await api._stop_observer([])


async def test_heartbeat_checker_skipped_when_heartbeat_off(monkeypatch) -> None:
    from app.services import supervisor_heartbeat
    from app.web import api

    monkeypatch.setattr(settings, "engine_startup", True)
    monkeypatch.setenv("VP_SUPERVISOR_HEARTBEAT", "0")
    monkeypatch.setattr(observation_mod, "get_observer", lambda: _FakeObserver())

    async def _checker():
        raise AssertionError("сторож пульса поднят при VP_SUPERVISOR_HEARTBEAT=0")

    monkeypatch.setattr(supervisor_heartbeat, "run_checker", _checker)
    tasks = api._start_observer()
    assert [t.get_name() for t in tasks] == ["observation-consumer"]
    await api._stop_observer(tasks)


async def _run_lifespan(app) -> list[dict]:
    received = [{"type": "lifespan.startup"}]
    sent: list[dict] = []

    async def receive():
        if received:
            return received.pop(0)
        await asyncio.sleep(0.2)  # цикл успевает стартовать до остановки
        return {"type": "lifespan.shutdown"}

    async def send(message):
        sent.append(message)

    await app({"type": "lifespan"}, receive, send)
    return sent


async def test_full_lifespan_runs_the_observer_and_stops_it(monkeypatch, heartbeat_dir) -> None:
    """Настоящий lifespan: цикл поднят на старте, погашен на остановке, пульс «остановлен»."""
    from app.orchestrator.agents import approval_expiry
    from app.orchestrator.agents import runtime as rt_mod
    from app.services import orch_bridge, supervisor_heartbeat
    from app.web.api import create_app

    monkeypatch.setattr(settings, "studio_session_secret", "")
    monkeypatch.setattr(settings, "web_host", "127.0.0.1")
    monkeypatch.setattr(settings, "engine_startup", True)
    monkeypatch.delenv("ORCH_RECOVER_ON_START", raising=False)
    monkeypatch.delenv("VP_SUPERVISOR_HEARTBEAT", raising=False)

    class _Sup:
        async def settle_after_restart(self):
            return []

    class _Rt:
        supervisor = _Sup()

    monkeypatch.setattr(rt_mod, "get_runtime", lambda: _Rt())

    async def _close():
        return []

    monkeypatch.setattr(orch_bridge, "close_interrupted_plans", _close)
    monkeypatch.setattr(approval_expiry, "start_approval_expiry", lambda s: None)
    fake = _FakeObserver()
    monkeypatch.setattr(observation_mod, "get_observer", lambda: fake)
    checker: list[asyncio.Task] = []

    async def _checker():
        checker.append(asyncio.current_task())
        await asyncio.sleep(3600)

    monkeypatch.setattr(supervisor_heartbeat, "run_checker", _checker)

    sent = await _run_lifespan(create_app())
    kinds = [m.get("type") for m in sent]
    assert "lifespan.startup.complete" in kinds and "lifespan.shutdown.complete" in kinds, sent
    assert fake.started == 1
    assert len(checker) == 1 and checker[0].cancelled()
    hb = supervisor_heartbeat.read_heartbeat(heartbeat_dir)
    assert hb is not None and hb["stopped"] is True
