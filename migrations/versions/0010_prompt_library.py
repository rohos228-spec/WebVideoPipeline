"""промт-библиотека как данные: переопределение на бренд, арендатора и проект

Revision ID: 0010
Revises: 0009
Create Date: 2026-08-24

Библиотека живёт в `prompts/`, каталог намеренно вне git. Отсюда две беды
сразу: на чистом клоне падает полсотни тестов на отсутствии ДАННЫХ, а не
кода, и в SaaS клиенту нечего править — диска узла у него нет, а «поменять
промпт на более лучший» это ровно то, зачем он пришёл (§9.4).

Таблица даёт четыре уровня переопределения — проект, арендатор, бренд,
системный, — и каждый отвечает на свой вопрос: системный «как правильно»,
бренд «как принято у нас», арендатор «как хочу я», проект «как в этом
ролике». Схлопнуть их в один значит однажды переписывать, а не добавлять
строку.

Файл на диске остаётся последним звеном цепочки: он источник правды для
режима владельца и он же наполняет системный уровень загрузчиком.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "prompt_library"


def upgrade() -> None:
    # Как в 0007–0009: baseline (0001) создаёт схему из моделей, поэтому на
    # пустой базе таблица уже есть.
    bind = op.get_bind()
    if TABLE in set(sa.inspect(bind).get_table_names()):
        return
    op.create_table(
        TABLE,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(as_uuid=False), nullable=True),
        sa.Column("brand", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("project_id", sa.Integer(), nullable=True),
        sa.Column("step_code", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False, server_default="default"),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("tenant_id", "brand", "project_id", "step_code", "name", name="uq_prompt_scope"),
    )
    op.create_index(f"ix_{TABLE}_tenant_id", TABLE, ["tenant_id"])
    op.create_index(f"ix_{TABLE}_project_id", TABLE, ["project_id"])
    op.create_index(f"ix_{TABLE}_step_code", TABLE, ["step_code"])
    op.create_index("ix_prompt_library_lookup", TABLE, ["step_code", "name"])


def downgrade() -> None:
    if TABLE in set(sa.inspect(op.get_bind()).get_table_names()):
        op.drop_table(TABLE)
