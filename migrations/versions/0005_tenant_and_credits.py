"""tenant_id во все таблицы данных + таблицы кредитов

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-23

Схема арендатора и кассы (docs/SAAS-PIVOT.md §4.1, §5.3). Ревизия только
структурная: политики row-level security выделены в 0006, потому что они
существуют исключительно в Postgres, а эта ревизия обязана пройти и на
SQLite — иначе на нём нельзя ни поднять систему владельца, ни прогнать
тесты.

`tenant_id` добавляется **обнуляемым**. Данные, заведённые до перехода в
SaaS, принадлежат владельцу и арендатора не имеют; проставить им выдуманный
UUID значило бы соврать в единственной таблице, по которой потом считают
деньги. Политика 0006 пропускает такие строки только в режиме одного
арендатора.

`fleet_nodes` и `work_leases` колонки не получают намеренно: это машины
парка и блокировки шагов — инфраструктура узла, а не данные клиента.
Арендатор у лизинга был бы бессмыслицей: лизинг принадлежит воркеру.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.services.tenant_tables import TENANT_TABLES

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Тип арендатора: нативный UUID в Postgres, CHAR(32) в SQLite.
_TENANT = sa.Uuid(as_uuid=False)


def _has_column(inspector: sa.Inspector, table: str, column: str) -> bool:
    if table not in inspector.get_table_names():
        return False
    return any(col["name"] == column for col in inspector.get_columns(table))


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    for table in TENANT_TABLES:
        if table not in tables:
            # Таблицы нет — значит нет и в моделях этой версии; baseline
            # создаст её уже с колонкой.
            continue
        if _has_column(inspector, table, "tenant_id"):
            continue
        op.add_column(table, sa.Column("tenant_id", _TENANT, nullable=True))
        op.create_index(f"ix_{table}_tenant_id", table, ["tenant_id"])

    if "credit_accounts" not in tables:
        op.create_table(
            "credit_accounts",
            sa.Column("tenant_id", _TENANT, primary_key=True),
            sa.Column("balance_micro", sa.BigInteger(), nullable=False, server_default="0"),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )

    if "credit_holds" not in tables:
        op.create_table(
            "credit_holds",
            sa.Column("id", _TENANT, primary_key=True),
            sa.Column("tenant_id", _TENANT, nullable=False),
            sa.Column("project_id", sa.Integer(), nullable=False),
            sa.Column("step_code", sa.String(length=40), nullable=False),
            sa.Column("node_key", sa.String(length=120), nullable=False, server_default=""),
            sa.Column("amount_micro", sa.BigInteger(), nullable=False),
            sa.Column("state", sa.String(length=12), nullable=False, server_default="held"),
            sa.Column("expires_at", sa.DateTime(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_credit_holds_tenant_id", "credit_holds", ["tenant_id"])
        op.create_index("ix_credit_holds_project_id", "credit_holds", ["project_id"])
        op.create_index("ix_credit_holds_state", "credit_holds", ["state"])
        op.create_index("ix_credit_holds_expires_at", "credit_holds", ["expires_at"])
        op.create_index("ix_credit_holds_created_at", "credit_holds", ["created_at"])
        op.create_index("ix_credit_holds_open", "credit_holds", ["tenant_id", "state"])
        op.create_index("ix_credit_holds_project", "credit_holds", ["project_id", "step_code"])

    if "credit_entries" not in tables:
        op.create_table(
            "credit_entries",
            sa.Column("id", _TENANT, primary_key=True),
            sa.Column("tenant_id", _TENANT, nullable=False),
            sa.Column("hold_id", _TENANT, nullable=True),
            sa.Column("delta_micro", sa.BigInteger(), nullable=False),
            sa.Column("kind", sa.String(length=12), nullable=False),
            sa.Column("cost_usd", sa.Numeric(18, 8), nullable=True),
            sa.Column("margin", sa.Numeric(6, 3), nullable=True),
            sa.Column("ref_table", sa.String(length=20), nullable=False, server_default=""),
            sa.Column("ref_ids", sa.JSON(), nullable=False),
            sa.Column("memo", sa.String(length=240), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_credit_entries_tenant_id", "credit_entries", ["tenant_id"])
        op.create_index("ix_credit_entries_hold_id", "credit_entries", ["hold_id"])
        op.create_index("ix_credit_entries_kind", "credit_entries", ["kind"])
        op.create_index("ix_credit_entries_created_at", "credit_entries", ["created_at"])
        op.create_index(
            "ix_credit_entries_tenant_time", "credit_entries", ["tenant_id", "created_at"]
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    for table in ("credit_entries", "credit_holds", "credit_accounts"):
        if table in tables:
            op.drop_table(table)

    for table in TENANT_TABLES:
        if _has_column(inspector, table, "tenant_id"):
            op.drop_index(f"ix_{table}_tenant_id", table_name=table)
            op.drop_column(table, "tenant_id")
