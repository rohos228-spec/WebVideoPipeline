"""orch_llm_calls.provider: настоящий провайдер (vibecode), а не «52pay» по умолчанию

Revision ID: 0024
Revises: 0023
Create Date: 2026-10-24

CHECK колонки из 0021 (orch_tables) знал только `52pay` и `kie`, а маршрут текстовой модели оркестратора
бывает и `vibecode` (`app/orchestrator/routing/config.py`). Учёт (`routing/accounting.py`)
переписывал всё незнакомое в `52pay`: журнал вызовов показывал не того провайдера.
Теперь допустимы `52pay | kie | vibecode | unknown` (`unknown` — провайдер не назван).

* **Postgres:** CHECK меняется на месте (`orch_llm_calls_provider_check`).
* **SQLite:** файл раннера `011_orch_llm_calls_provider.sql` (пересборка таблицы с теми же
  строками и индексами) и запись в его журнал — схема байт в байт как у раннера.

Откат возвращает прежний CHECK на Postgres; строки с `vibecode`/`unknown` при этом
становятся `52pay` (иначе старый CHECK не встанет). На SQLite откат только убирает запись
журнала: более широкий CHECK старому коду не мешает, повторный upgrade пересоберёт таблицу.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0024"
down_revision: str | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RUNNER_FILE = "011_orch_llm_calls_provider.sql"
_CONSTRAINT = "orch_llm_calls_provider_check"
_NEW = "provider IN ('52pay', 'kie', 'vibecode', 'unknown')"
_OLD = "provider IN ('52pay', 'kie')"


def _runner_sqlite(bind: sa.engine.Connection) -> None:
    from app.orchestrator.db.migrations import JOURNAL_DDL, sqlite_statements, utc_now

    if not sa.inspect(bind).has_table("orch_llm_calls"):
        return
    bind.exec_driver_sql(JOURNAL_DDL)
    applied = {row[0] for row in bind.exec_driver_sql("SELECT name FROM orch_schema_migrations")}
    if RUNNER_FILE in applied:
        return

    def has_column(table: str, column: str) -> bool:
        inspector = sa.inspect(bind)
        if not inspector.has_table(table):
            return False
        return any(col["name"] == column for col in inspector.get_columns(table))

    for statement in sqlite_statements(RUNNER_FILE, has_column):
        bind.exec_driver_sql(statement)
    bind.execute(
        sa.text("INSERT INTO orch_schema_migrations (name, applied_at) VALUES (:name, :at)"),
        {"name": RUNNER_FILE, "at": utc_now()},
    )


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        _runner_sqlite(bind)
        return
    if not sa.inspect(bind).has_table("orch_llm_calls"):
        return
    op.execute(f"ALTER TABLE orch_llm_calls DROP CONSTRAINT IF EXISTS {_CONSTRAINT}")
    op.execute(f"ALTER TABLE orch_llm_calls ADD CONSTRAINT {_CONSTRAINT} CHECK ({_NEW})")


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        if sa.inspect(bind).has_table("orch_schema_migrations"):
            bind.execute(
                sa.text("DELETE FROM orch_schema_migrations WHERE name = :name"), {"name": RUNNER_FILE}
            )
        return
    if not sa.inspect(bind).has_table("orch_llm_calls"):
        return
    op.execute(f"ALTER TABLE orch_llm_calls DROP CONSTRAINT IF EXISTS {_CONSTRAINT}")
    op.execute("UPDATE orch_llm_calls SET provider = '52pay' WHERE provider NOT IN ('52pay', 'kie')")
    op.execute(f"ALTER TABLE orch_llm_calls ADD CONSTRAINT {_CONSTRAINT} CHECK ({_OLD})")
