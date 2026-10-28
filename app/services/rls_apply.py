"""Включение RLS на таблицах, которые создаёт не alembic, а create_all.

Таблицы слоя действий и контроля изменений заводятся в lifespan через
`Base.metadata.create_all` (`app/web/api.py::_ensure_engine_schema`). Миграция
0006 на них не действует: их ещё нет, когда 0006 уже отработала. Без
политик `assert_rls_or_die` либо ругается на «таблицы вне списков», либо
(после добавления в TENANT_TABLES) на отсутствие ENABLE/FORCE/политики —
и uvicorn не доходит до /api/health. Deploy healthcheck видит unhealthy
и откатывает выкладку.

Вызов идемпотентный: повторный ENABLE/FORCE и DROP+CREATE POLICY безопасны.
На SQLite — no-op (RLS там нет).
"""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import inspect, text

from app.services.tenant_tables import DEFAULT_POLICY

# Условие с поддержкой режима администратора (app.is_admin = 'true')
_PREDICATE = """(
    CASE
        WHEN current_setting('app.is_admin', true) = 'true'
            THEN true
        WHEN nullif(current_setting('app.tenant_id', true), '') IS NULL
            THEN tenant_id IS NULL
        ELSE tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid
    END
)"""


def apply_tenant_isolation_sync(connection, tables: Iterable[str]) -> None:
    """ENABLE + FORCE + CREATE POLICY tenant_isolation на существующих таблицах."""
    if connection.dialect.name != "postgresql":
        return
    existing = set(inspect(connection).get_table_names())
    policy = DEFAULT_POLICY
    for table in tables:
        if table not in existing:
            continue
        connection.execute(text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
        connection.execute(text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))
        connection.execute(text(f"DROP POLICY IF EXISTS {policy} ON {table}"))
        connection.execute(
            text(
                f"CREATE POLICY {policy} ON {table} "
                f"USING {_PREDICATE} WITH CHECK {_PREDICATE}"
            )
        )
