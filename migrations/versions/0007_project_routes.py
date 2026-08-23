"""маршрутная таблица проектов: воркер находит работу через политики RLS

Revision ID: 0007
Revises: 0006
Create Date: 2026-08-23

Воркер конвейера ищет работу сканированием: «дай все проекты в рабочем
статусе». Под row-level security такой запрос невозможен по построению —
сканирование межарендно, а RLS запрещает межарендное чтение, в том и смысл.
Воркер, не назначивший арендатора, видит проекты владельца и ни одного
клиентского. Отказ бесшумный: петля жива, крутится, ничего не находит.

Ревизия вводит `project_routes` — **маршрутные факты и только они**: номер
проекта, его арендатор, его статус. Содержимого нет никакого; темы, тексты,
промты и цены остаются в `projects` под обычной политикой.

**Синхронность держит триггер, а не приложение.** Статус проекта меняют
десятки мест, часть — массовыми `UPDATE` мимо ORM. Требование «не забыть
обновить маршрут» разошлось бы с реальностью на первой же забытой строке, и
разошлось бы молча: воркер перестал бы видеть чей-то проект. Триггер обойти
из приложения нельзя.

**Политика обратная обычной, и это осознанно.** У всех остальных таблиц
незаданный арендатор означает «видно только ничьё»: забытая привязка не
должна становиться утечкой. Здесь сессия без арендатора видит все строки —
потому что режим без арендатора и есть служебный режим воркера, а иначе он
не найдёт работу вовсе. Сессия с арендатором видит только свои строки, то
есть клиент не может перечислить чужие проекты. Цена асимметрии ограничена
тем, что перечислять в этой таблице нечего, кроме номеров и статусов.

На SQLite создаётся только таблица. Ни политик, ни триггера там не нужно:
арендаторов на SQLite нет (`tenant.require_isolation`), и воркер работает
единственным проходом, как работал всегда.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

POLICY = "tenant_routes"
TABLE = "project_routes"
TRIGGER = "trg_project_routes_sync"
FUNCTION = "project_routes_sync"

#: Видимость строки. Обратная обычной: пусто — служебный режим, видно всё.
_PREDICATE = """(
    CASE
        WHEN nullif(current_setting('app.tenant_id', true), '') IS NULL
            THEN true
        ELSE tenant_id IS NOT DISTINCT FROM
             nullif(current_setting('app.tenant_id', true), '')::uuid
    END
)"""

#: Зеркало статуса. `DELETE` убирает строку, всё остальное — UPSERT.
#: `NEW.status::text` — не украшение: в Postgres это нативный enum
#: `project_status`, и без приведения функция не скомпилируется.
#:
#: Функция намеренно ничего не знает про то, какие статусы «рабочие»: список
#: рабочих статусов живёт в Python (`node_registry.WORKER_ACTIVE_STATUSES`),
#: и второй его копии в SQL быть не должно — они разошлись бы молча.
_FUNCTION_SQL = f"""
CREATE OR REPLACE FUNCTION {FUNCTION}() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF (TG_OP = 'DELETE') THEN
        DELETE FROM {TABLE} WHERE project_id = OLD.id;
        RETURN OLD;
    END IF;
    INSERT INTO {TABLE} (project_id, tenant_id, status_value, updated_at)
    VALUES (NEW.id, NEW.tenant_id, NEW.status::text, now())
    ON CONFLICT (project_id) DO UPDATE
        SET tenant_id = EXCLUDED.tenant_id,
            status_value = EXCLUDED.status_value,
            updated_at = now();
    RETURN NEW;
END;
$$;
"""


def upgrade() -> None:
    # Таблица могла уже появиться: baseline (0001) создаёт схему из моделей,
    # а модель `ProjectRoute` там есть. На пустой базе 0001 её и создаёт, на
    # существующей — создаём здесь. Ревизия обязана быть безразлична к тому,
    # каким из двух путей пришла база, иначе новая инсталляция не поднимется.
    bind = op.get_bind()
    if TABLE not in set(sa.inspect(bind).get_table_names()):
        op.create_table(
            TABLE,
            sa.Column("project_id", sa.Integer(), primary_key=True, autoincrement=False),
            sa.Column("tenant_id", sa.Uuid(as_uuid=False), nullable=True),
            sa.Column("status_value", sa.String(length=40), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )
        op.create_index(f"ix_{TABLE}_tenant_id", TABLE, ["tenant_id"])
        op.create_index(f"ix_{TABLE}_status_value", TABLE, ["status_value"])
        op.create_index(f"ix_{TABLE}_tenant_status", TABLE, ["tenant_id", "status_value"])

    if bind.dialect.name != "postgresql":
        return

    op.execute(_FUNCTION_SQL)
    # Триггер на изменение статуса и арендатора, а не на любое обновление:
    # проект правят на каждом шаге, и зеркалить нечего, пока маршрут тот же.
    op.execute(
        f"CREATE TRIGGER {TRIGGER} "
        f"AFTER INSERT OR DELETE OR UPDATE OF status, tenant_id ON projects "
        f"FOR EACH ROW EXECUTE FUNCTION {FUNCTION}()"
    )
    # Заполнение по существующим проектам. Идёт из-под миграции, то есть без
    # арендатора: в этот момент политики на `projects` тоже не действуют
    # только для строк владельца — а на свежей базе строк нет вовсе.
    op.execute(
        f"INSERT INTO {TABLE} (project_id, tenant_id, status_value, updated_at) "
        f"SELECT id, tenant_id, status::text, now() FROM projects "
        f"ON CONFLICT (project_id) DO NOTHING"
    )

    op.execute(f"ALTER TABLE {TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {TABLE} FORCE ROW LEVEL SECURITY")
    op.execute(f"DROP POLICY IF EXISTS {POLICY} ON {TABLE}")
    op.execute(f"CREATE POLICY {POLICY} ON {TABLE} USING {_PREDICATE} WITH CHECK {_PREDICATE}")


def downgrade() -> None:
    bind = op.get_bind()
    if TABLE not in set(sa.inspect(bind).get_table_names()):
        return
    if bind.dialect.name == "postgresql":
        op.execute(f"DROP TRIGGER IF EXISTS {TRIGGER} ON projects")
        op.execute(f"DROP FUNCTION IF EXISTS {FUNCTION}()")
        op.execute(f"DROP POLICY IF EXISTS {POLICY} ON {TABLE}")
    op.drop_table(TABLE)
