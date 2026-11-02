"""Admin RLS bypass and backfill unowned projects to primary admin.

Revision ID: 0020
Revises: 0019
Create Date: 2026-10-28
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.services.tenant_tables import DEFAULT_POLICY, all_isolated

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PREDICATE = """(
    CASE
        WHEN current_setting('app.is_admin', true) = 'true'
            THEN true
        WHEN nullif(current_setting('app.tenant_id', true), '') IS NULL
            THEN tenant_id IS NULL
        ELSE tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid
    END
)"""

_OLD_PREDICATE = """(
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
    for table in all_isolated():
        if table not in tables:
            continue
        op.execute(f"DROP POLICY IF EXISTS {policy} ON {table}")
        op.execute(
            f"CREATE POLICY {policy} ON {table} USING {_PREDICATE} WITH CHECK {_PREDICATE}"
        )

    # Привязываем исторические проекты без tenant_id к первому администратору (Александр)
    # Временно отключаем RLS на целевых таблицах для миграции, чтобы не срабатывал WITH CHECK
    backfill_tables = ["projects", "artifacts", "media_calls", "llm_calls", "frames", "scenes"]
    for t in backfill_tables:
        if t in tables:
            op.execute(f"ALTER TABLE {t} NO FORCE ROW LEVEL SECURITY")
            op.execute(f"ALTER TABLE {t} DISABLE ROW LEVEL SECURITY")

    op.execute(
        """
        DO $$
        DECLARE
            admin_id uuid;
        BEGIN
            PERFORM set_config('app.is_admin', 'true', true);
            SELECT id INTO admin_id FROM studio_users WHERE role = 'admin' AND email LIKE '%rohos%' LIMIT 1;
            IF admin_id IS NULL THEN
                SELECT id INTO admin_id FROM studio_users WHERE role = 'admin' ORDER BY created_at ASC LIMIT 1;
            END IF;
            IF admin_id IS NOT NULL THEN
                UPDATE projects SET tenant_id = admin_id WHERE tenant_id IS NULL;
                UPDATE artifacts SET tenant_id = admin_id WHERE tenant_id IS NULL;
                UPDATE media_calls SET tenant_id = admin_id WHERE tenant_id IS NULL;
                UPDATE llm_calls SET tenant_id = admin_id WHERE tenant_id IS NULL;
                UPDATE frames SET tenant_id = admin_id WHERE tenant_id IS NULL;
                UPDATE scenes SET tenant_id = admin_id WHERE tenant_id IS NULL;
            END IF;
        END $$;
        """
    )

    for t in backfill_tables:
        if t in tables:
            op.execute(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")
            op.execute(f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    tables = set(sa.inspect(bind).get_table_names())
    policy = DEFAULT_POLICY
    for table in all_isolated():
        if table not in tables:
            continue
        op.execute(f"DROP POLICY IF EXISTS {policy} ON {table}")
        op.execute(
            f"CREATE POLICY {policy} ON {table} USING {_OLD_PREDICATE} WITH CHECK {_OLD_PREDICATE}"
        )
