from __future__ import annotations

import asyncio
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from loguru import logger
from sqlalchemy import text

from app.orchestrator.routing.types import LlmCallRecord

LEDGER_SOURCE = "orch_llm"
_ROLES = frozenset({"supervisor", "planner", "critic", "executor", "classifier", "summarizer"})
_TIERS = frozenset({"fast", "strong", "auto"})
# Провайдеры маршрута (routing/config.py: 52pay | kie | vibecode). Незнакомое имя — «unknown»,
# а не «52pay»: иначе журнал вызовов приписывал vibecode чужому провайдеру (CHECK — миграция 011).
_PROVIDERS = frozenset({"52pay", "kie", "vibecode"})
_STATUSES = frozenset({"ok", "timeout", "error", "fixture_miss"})
_IMPLS = frozenset({"live", "replay", "scripted"})
_tasks: set[asyncio.Task[Any]] = set()


def _pick(value: Any, allowed: frozenset[str], default: str) -> str:
    text = str(value or "").strip().lower()
    return text if text in allowed else default


def _project_of(conn: Any, task_id: str | None) -> int | None:
    if task_id is None or not str(task_id).strip().isdigit():
        return None
    row = conn.execute(
        text("SELECT project_id FROM orch_tasks WHERE id = :id"), {"id": int(task_id)}
    ).fetchone()
    if row is None or row[0] is None:
        return None
    try:
        value = int(row[0])
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def insert_call(row: LlmCallRecord) -> int | None:
    from app.orchestrator.db.migrations import connect

    conn = connect()
    try:
        # Повтор той же записи (тот же call_id) — не ошибка: ON CONFLICT
        # одинаково понимают SQLite и Postgres, в отличие от INSERT OR IGNORE.
        conn.execute(
            text(
                """
                INSERT INTO orch_llm_calls (
                    call_id, task_id, run_id, agent_role, requested_tier, tier, model, provider,
                    reasoning_effort, fallback_index, attempt, status, error, deterministic_flag,
                    classifier_confidence, latency_ms, prompt_tokens, completion_tokens, reasoning_tokens,
                    cost_usd, budget_scope, router_impl, messages_sha256, fixture_key, created_at
                ) VALUES (
                    :call_id, :task_id, :run_id, :agent_role, :requested_tier, :tier, :model, :provider,
                    :reasoning_effort, :fallback_index, :attempt, :status, :error, :deterministic_flag,
                    :classifier_confidence, :latency_ms, :prompt_tokens, :completion_tokens, :reasoning_tokens,
                    :cost_usd, :budget_scope, :router_impl, :messages_sha256, :fixture_key, :created_at
                )
                ON CONFLICT (call_id) DO NOTHING
                """
            ),
            {
                "call_id": row.call_id,
                "task_id": None if row.task_id is None else str(row.task_id),
                "run_id": None if row.run_id is None else str(row.run_id),
                "agent_role": _pick(row.agent_role, _ROLES, "supervisor"),
                "requested_tier": _pick(row.requested_tier, _TIERS, "auto"),
                "tier": _pick(row.tier, _TIERS, "strong"),
                "model": str(row.model or "unknown"),
                "provider": _pick(row.provider, _PROVIDERS, "unknown"),
                "reasoning_effort": row.reasoning_effort,
                "fallback_index": int(row.fallback_index or 0),
                "attempt": int(row.attempt or 1),
                "status": _pick(row.status, _STATUSES, "error"),
                "error": (str(row.error)[:500] if row.error else None),
                "deterministic_flag": 1 if row.deterministic_flag else 0,
                "classifier_confidence": row.classifier_confidence,
                "latency_ms": int(row.latency_ms or 0),
                "prompt_tokens": int(row.prompt_tokens or 0),
                "completion_tokens": int(row.completion_tokens or 0),
                "reasoning_tokens": int(row.reasoning_tokens or 0),
                "cost_usd": float(row.cost_usd or 0.0),
                "budget_scope": _pick(row.budget_scope, frozenset({"internal", "pipeline"}), "internal"),
                "router_impl": _pick(row.router_impl, _IMPLS, "live"),
                "messages_sha256": row.messages_sha256 or None,
                "fixture_key": row.fixture_key,
                "created_at": row.created_at or "",
            },
        )
        project_id = _project_of(conn, row.task_id)
        conn.commit()
    finally:
        conn.close()
    return project_id


def ledger_note(row: LlmCallRecord) -> str:
    basis = "basis=estimate tokens=estimated" if row.usage_estimated else "basis=computed tokens=reported"
    return (
        f"{basis} model={row.model} role={row.agent_role or '-'} status={row.status} "
        f"tokens={row.prompt_tokens}/{row.completion_tokens}/{row.reasoning_tokens}"
    )


async def write_spend(row: LlmCallRecord, project_id: int | None) -> None:
    from app.services import spend_guard

    try:
        async with spend_guard._sessions()() as session:
            await spend_guard.record_actual(
                session,
                project_id=project_id,
                usd=float(row.cost_usd),
                provider=str(row.provider or ""),
                source=LEDGER_SOURCE,
                run_id=str(row.run_id or ""),
                note=ledger_note(row),
                kind="estimated",
                ref=str(row.call_id or ""),
            )
            await session.commit()
    except Exception:
        spend_guard._note_write_failure("orch_llm_spend")


def _ledger_path() -> Path | None:
    from app.services import spend_guard

    try:
        factory = spend_guard._sessions()
    except Exception:
        return None
    bind = (getattr(factory, "kw", None) or {}).get("bind")
    url = getattr(bind, "url", None)
    if url is None or not str(getattr(url, "drivername", "")).startswith("sqlite"):
        return None
    database = str(url.database or "")
    if not database or database == ":memory:":
        return None
    return Path(database)


def _spend_values(row: LlmCallRecord, project_id: int | None) -> tuple[Any, ...]:
    from app.services import spend_guard

    return (
        datetime.now(UTC).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S.%f"),
        spend_guard._today(),
        "estimated",
        str(row.provider or "")[:32],
        project_id,
        (str(row.run_id or "") or spend_guard._ctx_run_id())[:80],
        LEDGER_SOURCE,
        spend_guard._valid(float(row.cost_usd)),
        0.0,
        spend_guard._clean(ledger_note(row)),
        spend_guard._ctx_request_id()[:64],
        str(row.call_id or "")[:120],
    )


def insert_spend(path: Path, values: tuple[Any, ...]) -> None:
    conn = sqlite3.connect(str(path), timeout=30)
    try:
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute(
            """
            INSERT INTO spend_ledger (
                created_at, day, kind, provider, project_id, run_id, source, usd, credits, note, request_id, ref
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            values,
        )
        conn.commit()
    finally:
        conn.close()


def _schema_missing(exc: BaseException) -> bool:
    text = str(exc).lower()
    return "no such table" in text or "no column" in text or "no such column" in text


def _wants_spend(row: LlmCallRecord, spend: bool) -> bool:
    if not spend or row.router_impl != "live":
        return False
    try:
        return float(row.cost_usd or 0.0) > 0
    except (TypeError, ValueError):
        return False


def _insert_call_logged(row: LlmCallRecord) -> int | None:
    try:
        return insert_call(row)
    except Exception:
        logger.exception("orch_llm_calls: row not written ({})", row.call_id)
        return None


async def book(row: LlmCallRecord, spend: bool) -> None:
    from app.services import spend_guard

    project_id = await asyncio.to_thread(_insert_call_logged, row)
    if not _wants_spend(row, spend):
        return
    path = _ledger_path()
    if path is not None:
        try:
            await asyncio.to_thread(insert_spend, path, _spend_values(row, project_id))
            return
        except Exception as exc:
            if not _schema_missing(exc):
                spend_guard._note_write_failure("orch_llm_spend")
                return
    await write_spend(row, project_id)


def _schedule(job: Any) -> asyncio.Task[Any] | None:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        job.close()
        return None
    task = loop.create_task(job)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return task


async def drain() -> None:
    pending = [task for task in list(_tasks) if not task.done()]
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


class DbLlmCallSink:
    def __init__(self, *, spend: bool = True) -> None:
        self.spend = spend

    def record(self, row: LlmCallRecord) -> None:
        if _schedule(book(row, self.spend)) is not None:
            return
        project_id = _insert_call_logged(row)
        if not _wants_spend(row, self.spend):
            return
        path = _ledger_path()
        if path is None:
            return
        try:
            insert_spend(path, _spend_values(row, project_id))
        except Exception:
            from app.services import spend_guard

            spend_guard._note_write_failure("orch_llm_spend")


def attach(router: Any) -> bool:
    current = getattr(router, "sink", None)
    if current is None or type(current).__name__ != "NullLlmCallSink":
        return False
    router.sink = DbLlmCallSink()
    return True
