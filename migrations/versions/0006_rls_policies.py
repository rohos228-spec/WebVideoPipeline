"""row-level security: изоляция арендаторов средствами Postgres

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-23

На SQLite ревизия не делает ничего: RLS в этом движке отсутствует. Именно
поэтому многоарендный режим на SQLite запрещён явной проверкой
(`app/services/tenant.py::require_isolation`) — молча работать без изоляции
опаснее, чем не запуститься.

**Почему FORCE, а не просто ENABLE.** `ENABLE ROW LEVEL SECURITY` не
действует на владельца таблиц. Приложение почти всегда подключается ровно
владельцем — то есть политики были бы созданы, видны в `\\d+`, и при этом не
применялись бы ни к одному запросу. Отказ был бы бесшумным, а обнаружился бы
утечкой чужого ролика. `FORCE` распространяет политику и на владельца.

Обойти RLS всё ещё может суперпользователь и роль с атрибутом `BYPASSRLS` —
это не лечится политикой, это вопрос того, под кем ходит приложение.
Проверка вынесена в `app/services/rls_check.py`, её же зовёт health-ручка.

**Ноль как «не задано».** `current_setting('app.tenant_id', true)` возвращает
NULL, если настройку не выставляли, и пустую строку, если выставили пустую.
Оба случая означают «арендатора нет» — режим владельца, в котором видны
строки с `tenant_id IS NULL` и только они. Строки живых арендаторов в этом
режиме не видны никому, включая владельца: смотреть чужие ролики поштучно
можно из админской роли с `BYPASSRLS`, а не потому, что забыли настройку.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.services.tenant_tables import all_isolated

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

POLICY = "tenant_isolation"

#: Условие видимости строки. Одно и то же для чтения и для записи: строка,
#: которую нельзя прочитать, не должна быть и записываемой — иначе арендатор
#: пишет в чужое пространство вслепую.
_PREDICATE = """(
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
    for table in all_isolated():
        if table not in tables:
            continue
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS {POLICY} ON {table}")
        op.execute(
            f"CREATE POLICY {POLICY} ON {table} "
            f"USING {_PREDICATE} WITH CHECK {_PREDICATE}"
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    tables = set(sa.inspect(bind).get_table_names())
    for table in all_isolated():
        if table not in tables:
            continue
        op.execute(f"DROP POLICY IF EXISTS {POLICY} ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
