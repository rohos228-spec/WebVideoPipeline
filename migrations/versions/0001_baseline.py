"""baseline: схема из app.models

Revision ID: 0001
Revises:
Create Date: 2026-08-22

Точка отсчёта. До этой ревизии истории миграций не было вообще: схема
создавалась `Base.metadata.create_all` на старте, а колонки, которых
create_all добавить не умеет, дописывались ad-hoc блоками `ALTER TABLE`
в `app/main.py::_init_db` и `app/services/db_v2.migrate_db_v2_schema`.
Следствие: изменение модели молча не доезжало до существующих баз, а
добавление значения в enum с CHECK-констрейнтом требовало полного reset.

Baseline не «сочиняет» схему заново — он материализует ровно то, что
описано в `app.models.Base.metadata`. Для существующих баз runner
(`app/db_migrations.py`) вместо прогона делает `stamp 0001`: таблицы там
уже есть, переклад не нужен.

downgrade намеренно не реализован: единственный осмысленный «откат»
baseline — снести базу заказчика целиком.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from app.models import Base

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    raise NotImplementedError(
        "0001 — baseline; откат означал бы удаление всех таблиц. Если нужна чистая база: rm -f data/state.db"
    )
