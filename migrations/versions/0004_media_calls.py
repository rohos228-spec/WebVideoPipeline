"""media_calls — учёт медиа-генераций

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-22

Первая ревизия, которая делает то, ради чего заводился alembic: добавляет
таблицу существующим базам. До этого `create_all` создал бы её только там,
где базы ещё нет.

Симметрична `llm_calls`, но без токенов: у медиа-провайдеров нет `usage`,
платят за единицы (кадр / секунда видео / символ). Подробности — в
`app/services/media_ledger.py`.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    existing = {r[0] for r in bind.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='table'")}
    if "media_calls" in existing:
        return

    op.create_table(
        "media_calls",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=True),
        sa.Column("node_key", sa.String(length=120), nullable=False),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("model", sa.String(length=120), nullable=False),
        sa.Column("units", sa.Float(), nullable=False),
        sa.Column("unit", sa.String(length=20), nullable=False),
        sa.Column("cost_usd", sa.Float(), nullable=False),
        sa.Column("result", sa.String(length=10), nullable=False),
        sa.Column("error_kind", sa.String(length=60), nullable=False),
        sa.Column("unpriced", sa.Boolean(), nullable=False),
        sa.Column("external_id", sa.String(length=120), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
    )
    op.create_index("ix_media_calls_created_at", "media_calls", ["created_at"])
    op.create_index("ix_media_calls_project_id", "media_calls", ["project_id"])
    op.create_index("ix_media_calls_project_created", "media_calls", ["project_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_media_calls_project_created", table_name="media_calls")
    op.drop_index("ix_media_calls_project_id", table_name="media_calls")
    op.drop_index("ix_media_calls_created_at", table_name="media_calls")
    op.drop_table("media_calls")
