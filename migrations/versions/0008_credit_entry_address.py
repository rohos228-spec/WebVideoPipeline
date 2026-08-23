"""проводке нужен адрес: проект и шаг

Revision ID: 0008
Revises: 0007
Create Date: 2026-08-23

У списания проект и шаг несёт холд: `credit_entries.hold_id` → `credit_holds`.
У промо-проводки бесплатного уровня (§5.7) холда нет вовсе — она пишется с
нулевой дельтой и без резерва, потому что резервировать нечего: платит
платформа. Такая проводка получалась безадресной.

Это ломало ровно то, ради чего §5.7 велит вести счётчики в леджере, а не в
отдельной подсистеме: нельзя ни сказать, во сколько обошлось привлечение
одного проекта, ни сосчитать, сколько бесплатных проектов уже завёл
арендатор. Две колонки дешевле отдельной таблицы счётчиков и не могут с ней
разойтись.

Обратной несовместимости нет: колонки обнуляемые, старые проводки остаются
как были — их адрес по-прежнему читается через холд.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "credit_entries"


def _columns(bind) -> set[str]:
    inspector = sa.inspect(bind)
    if TABLE not in set(inspector.get_table_names()):
        return set()
    return {c["name"] for c in inspector.get_columns(TABLE)}


def upgrade() -> None:
    # Как и в 0007: baseline (0001) создаёт схему из моделей, поэтому на
    # пустой базе колонки уже есть. Ревизия обязана быть безразлична к тому,
    # каким путём пришла база.
    bind = op.get_bind()
    existing = _columns(bind)
    if not existing:
        return
    if "project_id" not in existing:
        op.add_column(TABLE, sa.Column("project_id", sa.Integer(), nullable=True))
        op.create_index(f"ix_{TABLE}_project_id", TABLE, ["project_id"])
    if "step_code" not in existing:
        op.add_column(
            TABLE, sa.Column("step_code", sa.String(length=40), nullable=False, server_default="")
        )
        op.create_index(f"ix_{TABLE}_step_code", TABLE, ["step_code"])


def downgrade() -> None:
    bind = op.get_bind()
    existing = _columns(bind)
    if "step_code" in existing:
        op.drop_index(f"ix_{TABLE}_step_code", table_name=TABLE)
        op.drop_column(TABLE, "step_code")
    if "project_id" in existing:
        op.drop_index(f"ix_{TABLE}_project_id", table_name=TABLE)
        op.drop_column(TABLE, "project_id")
