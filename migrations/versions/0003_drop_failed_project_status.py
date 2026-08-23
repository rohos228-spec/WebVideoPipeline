"""data-fix: projects.status 'failed' → 'new'

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-22

Статус `failed` для проекта отменён (политика падений — в
`app/services/step_failure_policy.py`), но в базах, поднятых раньше, строки
с ним остаются и ломают загрузку enum'а. Правка жила прямо в `_init_db` как
`UPDATE ... WHERE status = 'failed'` в try/except; здесь она выполняется
один раз и фиксируется в истории.

`recompute_all` на старте поднимет проект из `new` до фактического уровня по
данным, поэтому обнуление безопасно.

downgrade нет: вернуть `failed` конкретным проектам неоткуда — какие именно
строки его имели, после апгрейда неизвестно.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    # Инспектор вместо PRAGMA: ревизия обязана проходить и на Postgres.
    if "projects" not in sa.inspect(bind).get_table_names():
        return
    bind.exec_driver_sql("UPDATE projects SET status = 'new' WHERE status = 'failed'")


def downgrade() -> None:
    raise NotImplementedError("0003 — какие проекты были failed, после апгрейда неизвестно")
