"""RLS для таблиц движка (action_layer / change_control), создаваемых create_all.

Revision ID: 0019
Revises: 0018
Create Date: 2026-10-25

Таблицы слоя действий и контроля изменений не входят в alembic-цепочку:
их поднимает `create_all` в lifespan. На первой выкладке #98/#103 контейнер
успевал создать таблицы, после чего `assert_rls_or_die` падал на coverage /
отсутствии политик — healthcheck unhealthy, rollback. Миграция идемпотентно
навешивает ENABLE/FORCE/`tenant_isolation` на уже существующие таблицы;
runtime-путь дублирует это в `_ensure_engine_schema` через `rls_apply`.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.services.tenant_tables import DEFAULT_POLICY, ENGINE_TENANT_TABLES

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PREDICATE = """(
    CASE
        WHEN nullif(current_setting('app.tenant_id', true), '') IS NULL
            THEN tenant_id IS NULL
        ELSE tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid
    END
)"""


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    tables = set(sa.inspect(bind).get_table_names())
    policy = DEFAULT_POLICY
    for table in ENGINE_TENANT_TABLES:
        if table not in tables:
            continue
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS {policy} ON {table}")
        op.execute(
            f"CREATE POLICY {policy} ON {table} USING {_PREDICATE} WITH CHECK {_PREDICATE}"
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    tables = set(sa.inspect(bind).get_table_names())
    policy = DEFAULT_POLICY
    for table in ENGINE_TENANT_TABLES:
        if table not in tables:
            continue
        op.execute(f"DROP POLICY IF EXISTS {policy} ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
