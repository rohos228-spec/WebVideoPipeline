"""llm_calls.cost_usd: NULL как «цена неизвестна».

До этого колонка была NOT NULL default 0.0, и модель вне `llm_prices.json`
давала 0 — неотличимый от бесплатного вызова. Теперь неизвестная цена
пишется NULL; ноль остаётся только реально нулевым вызовам (кэш, отказ до
вызова). Отчёт и сверка трактуют NULL как unpriced.

На Postgres это `ALTER COLUMN ... DROP NOT NULL` — без переписывания
таблицы. На SQLite такого ALTER нет, поэтому batch-режим alembic (перестройка
таблицы); в тестах схема строится из моделей, где колонка уже nullable.

Revision ID: 0018
Revises: 0017
Create Date: 2026-10-22
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    with op.batch_alter_table("llm_calls") as batch:
        batch.alter_column("cost_usd", existing_type=sa.Float(), nullable=True)


def downgrade() -> None:
    with op.batch_alter_table("llm_calls") as batch:
        batch.alter_column(
            "cost_usd",
            existing_type=sa.Float(),
            nullable=False,
            server_default=sa.text("0"),
        )
