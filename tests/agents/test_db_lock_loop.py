from __future__ import annotations

import asyncio
import sqlite3
import time

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.models import Base
from app.orchestrator import events
from app.orchestrator.agents.task_store import TaskStore
from app.orchestrator.contracts import TaskStatus
from app.orchestrator.db.migrations import ensure_migrated, resolve_db_path
from app.orchestrator.routing import accounting
from app.orchestrator.routing.types import LlmCallRecord
from app.services import spend_guard
from tests.orch_pg_support import ENGINE_TESTS_ON_POSTGRES, SQLITE_ONLY_REASON

pytestmark = pytest.mark.skipif(ENGINE_TESTS_ON_POSTGRES, reason=SQLITE_ONLY_REASON)


@pytest.fixture
async def same_db(monkeypatch):
    path = ensure_migrated(resolve_db_path())
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{path}", poolclass=NullPool, connect_args={"timeout": 60}
    )

    from sqlalchemy import event

    def _pragmas(dbapi_connection, _record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=60000")
        cursor.close()

    event.listens_for(engine.sync_engine, "connect")(_pragmas)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as s:
        await spend_guard.ensure_schema(s)
        await s.commit()
    monkeypatch.setattr(spend_guard, "_sessions", lambda: sessions)
    yield sessions
    await accounting.drain()
    await engine.dispose()


def _record(**over) -> LlmCallRecord:
    base = dict(
        call_id="lock-call",
        task_id=None,
        run_id="orch-task-1",
        agent_role="planner",
        requested_tier="strong",
        tier="strong",
        model="gpt-5.6-sol",
        provider="52pay",
        reasoning_effort="high",
        fallback_index=0,
        attempt=1,
        status="ok",
        error=None,
        deterministic_flag=True,
        classifier_confidence=None,
        latency_ms=12,
        prompt_tokens=1000,
        completion_tokens=200,
        reasoning_tokens=300,
        cost_usd=0.0125,
        budget_scope="internal",
        router_impl="live",
        messages_sha256="abc",
        fixture_key=None,
        usage_estimated=False,
        created_at="2026-10-19T10:00:00Z",
    )
    base.update(over)
    return LlmCallRecord(**base)


async def _ledger(sessions) -> list[tuple]:
    async with sessions() as s:
        return (await s.execute(text("SELECT source, usd, project_id, ref, note FROM spend_ledger"))).all()


async def test_llm_booking_does_not_hold_write_lock_while_loop_thread_writes(same_db) -> None:
    tasks = TaskStore()
    task_id = tasks.insert("lock-a", "цель", TaskStatus.planning.value, project_id=7)
    accounting.DbLlmCallSink().record(_record(call_id="lock-a-call", task_id=str(task_id)))
    statuses = (TaskStatus.critic_review.value, TaskStatus.planning.value)
    turns = 0
    while any(not job.done() for job in list(accounting._tasks)) and turns < 5000:
        await asyncio.sleep(0)
        started = time.monotonic()
        tasks.set_status(task_id, statuses[turns % 2])
        assert time.monotonic() - started < 2.0
        turns += 1
    tasks.set_status(task_id, TaskStatus.critic_review.value)
    await accounting.drain()
    rows = await _ledger(same_db)
    assert len(rows) == 1
    assert rows[0][0] == accounting.LEDGER_SOURCE and rows[0][2] == 7 and rows[0][3] == "lock-a-call"
    assert rows[0][1] == pytest.approx(0.0125)
    assert tasks.get(task_id)["status"] == TaskStatus.critic_review.value


async def test_set_status_outwaits_async_writer_without_freezing_loop(runtime, same_db) -> None:
    supervisor = runtime.supervisor
    task_id = runtime.tasks.insert("lock-b", "цель", TaskStatus.planning.value)
    holding = asyncio.Event()

    async def holder() -> None:
        async with same_db() as s:
            await s.execute(text("UPDATE orch_tasks SET goal = goal WHERE id = :i"), {"i": task_id})
            holding.set()
            await asyncio.sleep(0.4)
            await s.commit()

    job = asyncio.create_task(holder())
    await holding.wait()
    started = time.monotonic()
    await supervisor._set_status(task_id, TaskStatus.critic_review, session_id="lock-b")
    await job
    assert time.monotonic() - started < 3.0
    assert runtime.tasks.get(task_id)["status"] == TaskStatus.critic_review.value


async def test_event_connection_recovers_after_lock_timeout() -> None:
    path = ensure_migrated(resolve_db_path())
    events._insert_event("lock-ev", "agent_status", {"n": 0}, task_id=None, created_at=None, persist=True)
    conn = events._CONNS[str(path)]
    conn.exec_driver_sql("PRAGMA busy_timeout=50")
    other = sqlite3.connect(str(path), timeout=0)
    try:
        other.execute("BEGIN IMMEDIATE")
        with pytest.raises(OperationalError, match="locked"):
            events._insert_event(
                "lock-ev", "agent_status", {"n": 1}, task_id=None, created_at=None, persist=True
            )
        other.execute("INSERT INTO orch_event_seq (session_id, seq) VALUES ('lock-other', 1)")
        other.commit()
    finally:
        other.close()
    assert not conn.in_transaction()
    events._insert_event("lock-ev", "agent_status", {"n": 2}, task_id=None, created_at=None, persist=True)
    assert events.persisted_types("lock-ev") == ["agent_status", "agent_status"]


class _FlakyStore(TaskStore):
    def __init__(self) -> None:
        super().__init__()
        self.fail: dict[tuple[int, str], int] = {}

    def set_status(self, task_id: int, status: str, error: str | None = None) -> None:
        left = self.fail.get((int(task_id), status), 0)
        if left:
            self.fail[(int(task_id), status)] = left - 1
            raise sqlite3.OperationalError("database is locked")
        super().set_status(task_id, status, error=error)


def _flaky_runtime():
    from app.orchestrator.agents.runtime import build_runtime, set_runtime
    from app.orchestrator.contracts import PlanStep
    from tests.agents.mocks import FixedPlanner, MockActions, MockValidators, ScriptedRouter

    runtime = build_runtime(
        actions=MockActions(),
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=FixedPlanner([PlanStep(action="look")]),
    )
    store = _FlakyStore()
    runtime.supervisor.tasks = store
    runtime.supervisor.status_write_budget_s = 0.3
    set_runtime(runtime)
    return runtime, store


def _task_events(session: str, task_id: int) -> list[tuple[str, str]]:
    from app.orchestrator.db.migrations import connect

    conn = connect()
    try:
        rows = (
            conn.execute(
                text(
                    "SELECT type, payload_json FROM orch_events WHERE session_id = :sid AND task_id = :task_id ORDER BY seq"
                ),
                {"sid": session, "task_id": task_id},
            )
            .mappings()
            .fetchall()
        )
    finally:
        conn.close()
    return [(row["type"], row["payload_json"]) for row in rows]


async def test_status_write_retries_and_task_finishes(token: str) -> None:
    from app.orchestrator.agents.runtime import set_runtime

    del token
    runtime, store = _flaky_runtime()
    try:
        runtime.supervisor.status_write_budget_s = 5.0
        task_id = await runtime.supervisor.submit_task("lock-retry", "посмотри")
        store.fail[(task_id, TaskStatus.critic_review.value)] = 2
        await runtime.supervisor.wait(task_id, timeout=10)
        assert store.get(task_id)["status"] == TaskStatus.done.value
        assert store.fail[(task_id, TaskStatus.critic_review.value)] == 0
    finally:
        set_runtime(None)


async def test_locked_status_write_fails_task_with_message_and_frees_queue(token: str) -> None:
    from app.orchestrator.agents.runtime import set_runtime

    del token
    session = "lock-fail"
    runtime, store = _flaky_runtime()
    supervisor = runtime.supervisor
    try:
        first = await supervisor.submit_task(session, "почему встал шаг картинок?")
        store.fail[(first, TaskStatus.critic_review.value)] = 10_000
        second = await supervisor.submit_task(session, "что с проектом")
        assert store.get(second)["status"] == TaskStatus.queued.value
        await supervisor.wait(first, timeout=10)
        row = store.get(first)
        assert row["status"] == TaskStatus.failed.value and row["error"] == "drive_failed"
        kinds = [kind for kind, _ in _task_events(session, first)]
        assert "task_failed" in kinds
        messages = [body for kind, body in _task_events(session, first) if kind == "message"]
        assert (
            messages and "Не получилось" in messages[-1] and "База данных Студии была занята" in messages[-1]
        )
        assert "OperationalError" not in messages[-1]
        await asyncio.sleep(0)
        await supervisor.wait(second, timeout=10)
        assert store.get(second)["status"] == TaskStatus.done.value
    finally:
        set_runtime(None)


async def test_unwritable_failed_status_still_frees_session_and_lands_later(token: str) -> None:
    from app.orchestrator.agents.runtime import set_runtime

    del token
    session = "lock-later"
    runtime, store = _flaky_runtime()
    supervisor = runtime.supervisor
    supervisor.failed_mark_delay_s = 0.05
    supervisor.failed_mark_retries = 200
    try:
        first = await supervisor.submit_task(session, "покажи ошибки")
        store.fail[(first, TaskStatus.critic_review.value)] = 10_000
        store.fail[(first, TaskStatus.failed.value)] = 10_000
        await supervisor.wait(first, timeout=10)
        assert store.get(first)["status"] == TaskStatus.planning.value
        assert supervisor.active_task(session) is None
        kinds = [kind for kind, _ in _task_events(session, first)]
        assert "task_failed" in kinds and "message" in kinds
        second = await supervisor.submit_task(session, "что с проектом")
        await supervisor.wait(second, timeout=10)
        assert store.get(second)["status"] == TaskStatus.done.value
        store.fail[(first, TaskStatus.failed.value)] = 0
        for _ in range(200):
            if store.get(first)["status"] == TaskStatus.failed.value:
                break
            await asyncio.sleep(0.05)
        assert store.get(first)["status"] == TaskStatus.failed.value
    finally:
        set_runtime(None)


async def test_crash_outside_drive_marks_task_failed(token: str) -> None:
    from app.orchestrator.agents.runtime import set_runtime

    del token
    session = "lock-crash"
    runtime, store = _flaky_runtime()
    supervisor = runtime.supervisor
    try:
        task_id = store.insert(session, "цель", TaskStatus.executing.value)

        async def boom() -> None:
            raise RuntimeError("сбой")

        job = supervisor._watch_run(task_id, session, asyncio.create_task(boom()))
        supervisor._runs[task_id] = job
        supervisor._session_task[session] = task_id
        with pytest.raises(RuntimeError):
            await job
        for _ in range(100):
            if store.get(task_id)["status"] == TaskStatus.failed.value:
                break
            await asyncio.sleep(0.02)
        assert store.get(task_id)["status"] == TaskStatus.failed.value
        assert supervisor.active_task(session) is None
    finally:
        set_runtime(None)


async def test_restart_without_recovery_closes_orphans_and_unblocks_chat(token: str) -> None:
    from app.orchestrator.agents.runtime import build_runtime, set_runtime
    from app.orchestrator.contracts import PlanStep
    from tests.agents.mocks import FixedPlanner, MockActions, MockValidators, ScriptedRouter

    del token
    tasks = TaskStore()
    planning = tasks.insert("rs-a", "почему встал шаг", TaskStatus.planning.value)
    queued = tasks.insert("rs-a", "следующая", TaskStatus.queued.value)
    executing = tasks.insert("rs-b", "сделай картинки", TaskStatus.executing.value, project_id=5)
    tasks.insert_step(executing, 0, PlanStep(action="node.run_step", paid=True, mutating=True))
    held = tasks.insert("rs-c", "ждёт кнопку", TaskStatus.waiting_approval.value)
    done = tasks.insert("rs-c", "готово", TaskStatus.done.value)
    runtime = build_runtime(
        actions=MockActions(),
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=FixedPlanner([PlanStep(action="look")]),
    )
    set_runtime(runtime)
    try:
        closed = await runtime.supervisor.settle_after_restart()
        assert closed == [planning, queued, executing]
        for task_id in closed:
            row = tasks.get(task_id)
            assert row["status"] == TaskStatus.failed.value and row["error"] == "interrupted_by_restart"
        assert tasks.get(held)["status"] == TaskStatus.waiting_approval.value
        assert tasks.get(done)["status"] == TaskStatus.done.value
        for session, task_id in (("rs-a", planning), ("rs-a", queued), ("rs-b", executing)):
            messages = [body for kind, body in _task_events(session, task_id) if kind == "message"]
            assert messages and "перезапуска сервера" in messages[-1]
        paid_note = [body for kind, body in _task_events("rs-b", executing) if kind == "message"][-1]
        assert "мог успеть выполниться" in paid_note
        fresh = await runtime.supervisor.submit_task("rs-a", "что с проектом")
        await runtime.supervisor.wait(fresh, timeout=10)
        assert tasks.get(fresh)["status"] == TaskStatus.done.value
        assert await runtime.supervisor.settle_after_restart() == []
    finally:
        set_runtime(None)


def test_startup_closes_orphans_when_recovery_is_off() -> None:
    from pathlib import Path

    source = Path("app/web/api.py").read_text(encoding="utf-8")
    # Проход идёт по каждому арендатору: метод передаётся в `for_each_tenant`.
    assert "supervisor.settle_after_restart" in source
    assert "for_each_tenant" in source
