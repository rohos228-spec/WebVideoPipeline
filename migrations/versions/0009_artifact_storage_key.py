"""ключ артефакта в объектном хранилище

Revision ID: 0009
Revises: 0008
Create Date: 2026-08-23

Артефакт сегодня описан одним путём на диске. Для одной машины этого хватает,
в SaaS — нет: файл виден только тому узлу, который его записал, а отдача идёт
через приложение и занимает воркер на всё время скачивания
(`docs/SAAS-PIVOT.md` §9.2).

Колонка добавляется рядом с путём, а не вместо него. Локальный файл нужен и
дальше: ffmpeg монтирует с диска, а не из сети. Пустой ключ означает «объект
ещё только на узле» — так выглядит режим владельца и так же выглядит
артефакт, который не успели опубликовать.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "artifacts"
COLUMN = "storage_key"


def _columns(bind) -> set[str]:
    inspector = sa.inspect(bind)
    if TABLE not in set(inspector.get_table_names()):
        return set()
    return {c["name"] for c in inspector.get_columns(TABLE)}


def upgrade() -> None:
    # Как в 0007 и 0008: baseline (0001) создаёт схему из моделей, поэтому на
    # пустой базе колонка уже есть.
    existing = _columns(op.get_bind())
    if existing and COLUMN not in existing:
        op.add_column(TABLE, sa.Column(COLUMN, sa.Text(), nullable=False, server_default=""))


def downgrade() -> None:
    if COLUMN in _columns(op.get_bind()):
        op.drop_column(TABLE, COLUMN)
