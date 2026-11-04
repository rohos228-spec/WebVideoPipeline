"""Повтор 009 после падения между ALTER и журналом не роняет старт."""

from __future__ import annotations

import pytest

from app.orchestrator.db.engine import get_engine
from app.orchestrator.db.migrations import apply_migrations, connect
from tests.orch_pg_support import ENGINE_TESTS_ON_POSTGRES, SQLITE_ONLY_REASON

pytestmark = pytest.mark.skipif(ENGINE_TESTS_ON_POSTGRES, reason=SQLITE_ONLY_REASON)

_NAME = "009_orch_tasks_project.sql"


def test_migration_009_rerun_after_crash_is_safe(token: str) -> None:
    del token
    connect().close()
    raw = get_engine().raw_connection()
    conn = raw.driver_connection
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(orch_tasks)")}
        assert "project_id" in columns
        conn.execute("DELETE FROM orch_schema_migrations WHERE name = ?", (_NAME,))
        conn.commit()
        applied = apply_migrations(conn)
        assert _NAME in applied
        columns = {row[1] for row in conn.execute("PRAGMA table_info(orch_tasks)")}
        assert "project_id" in columns
        journal = {row[0] for row in conn.execute("SELECT name FROM orch_schema_migrations")}
        assert _NAME in journal
    finally:
        raw.close()
