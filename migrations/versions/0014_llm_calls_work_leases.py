"""llm_calls + work_leases для баз, поднятых до их появления.

Revision ID: 0014
Revises: 0013
Create Date: 2026-09-17

Класс поломки AR-2: таблицы добавлены в `app.models` ПОСЛЕ baseline 0001
(`LlmCall`, `WorkLease`), а миграции под них не заведено. Свежие базы
получали таблицы через `create_all` в 0001 и всё было зелено, а базы
заказчика (stamp 0001 + upgrade) — нет: симптом
`(sqlite3.OperationalError) no such table: llm_calls`, учёт звонков
молча уходил в «очередь на дозапись».

Чинится созданием при отсутствии (идемпотентно, как 0004/0005).
Схема — 1:1 с моделями на дату ревизии.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())

    if "llm_calls" not in tables:
        op.create_table(
            "llm_calls",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("tenant_id", sa.Uuid(as_uuid=False), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("project_id", sa.Integer(), nullable=True),
            sa.Column("node_key", sa.String(length=120), nullable=False),
            sa.Column("logical_call_id", sa.String(length=36), nullable=False),
            sa.Column("model", sa.String(length=120), nullable=False),
            sa.Column("served_model", sa.String(length=120), nullable=False),
            sa.Column("relay", sa.String(length=120), nullable=False),
            sa.Column("endpoint", sa.String(length=20), nullable=False),
            sa.Column("prompt_tokens", sa.Integer(), nullable=True),
            sa.Column("completion_tokens", sa.Integer(), nullable=True),
            sa.Column("total_tokens", sa.Integer(), nullable=True),
            sa.Column("cost_usd", sa.Float(), nullable=False),
            sa.Column("result", sa.String(length=10), nullable=False),
            sa.Column("error_kind", sa.String(length=60), nullable=False),
            sa.Column("unbilled", sa.Boolean(), nullable=False),
            sa.Column("contract_rejected", sa.Boolean(), nullable=False),
            sa.Column("prompt_version_hash", sa.String(length=64), nullable=False),
            sa.Column("response_id", sa.String(length=120), nullable=False),
            sa.Column("duration_ms", sa.Integer(), nullable=False),
        )
        op.create_index("ix_llm_calls_tenant_id", "llm_calls", ["tenant_id"])
        op.create_index("ix_llm_calls_created_at", "llm_calls", ["created_at"])
        op.create_index("ix_llm_calls_project_id", "llm_calls", ["project_id"])
        op.create_index("ix_llm_calls_logical_call_id", "llm_calls", ["logical_call_id"])
        op.create_index("ix_llm_calls_project_created", "llm_calls", ["project_id", "created_at"])

    if "work_leases" not in tables:
        op.create_table(
            "work_leases",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("project_id", sa.Integer(), nullable=False),
            sa.Column("unit_key", sa.String(length=120), nullable=False),
            sa.Column("owner", sa.String(length=120), nullable=False),
            sa.Column("expires_at", sa.Float(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("project_id", "unit_key", name="uq_work_leases_unit"),
        )
        op.create_index("ix_work_leases_project_id", "work_leases", ["project_id"])
        op.create_index("ix_work_leases_expires_at", "work_leases", ["expires_at"])


def downgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "work_leases" in tables:
        op.drop_index("ix_work_leases_expires_at", table_name="work_leases")
        op.drop_index("ix_work_leases_project_id", table_name="work_leases")
        op.drop_table("work_leases")
    if "llm_calls" in tables:
        op.drop_index("ix_llm_calls_project_created", table_name="llm_calls")
        op.drop_index("ix_llm_calls_logical_call_id", table_name="llm_calls")
        op.drop_index("ix_llm_calls_project_id", table_name="llm_calls")
        op.drop_index("ix_llm_calls_created_at", table_name="llm_calls")
        op.drop_index("ix_llm_calls_tenant_id", table_name="llm_calls")
        op.drop_table("llm_calls")
