"""library_items и master_prompts: уникальность в пределах арендатора

Revision ID: 0013
Revises: 0012
Create Date: 2026-08-26

Первое сохранение промта из редактора на сервере — HTTP 500, в журнале
пусто. Причина:

    library_items  UNIQUE (kind, key)
    master_prompts UNIQUE (key, version)

Ключи без арендатора. Системная строка `prompts/01_plan/default.md`
(tenant_id IS NULL, импортирована на первом старте) под сессией арендатора
невидима — RLS прячет её честно. `get_item_by_key` возвращает None, код
вставляет «новую» строку с тем же ключом, и Postgres отбивает её уникальным
ограничением, которое арендаторов не различает. То же с `master_prompts`:
`version=1` фиксирован, ключ один на всех.

На SQLite этого нет: там нет RLS, системная строка видна, код её обновляет.
Вся суита зелёная, сервер — 500. Тот же класс, что varchar(20) и lease.

**Что делает.** Снимает оба ограничения и ставит вместо каждого пару
частичных уникальных индексов: один по (tenant_id, …) для строк арендаторов,
второй по (…) для системных (tenant_id IS NULL). Пара, а не один индекс с
`NULLS NOT DISTINCT`: тот требует Postgres 15+, а пара работает везде и
говорит ровно то, что имеется в виду — «системный уровень один, у каждого
арендатора свой».

**Данные не трогаются.** Существующие строки под новые индексы подходят:
дублей нет по построению — старое ограничение их не пускало.

SQLite: пропуск. Ограничения там не снять, RLS нет, а модели уже объявляют
новые ключи для свежих баз.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: (таблица, старое ограничение, колонки ключа без арендатора)
TARGETS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("library_items", "uq_library_item_kind_key", ("kind", "key")),
    ("master_prompts", "uq_prompt_key_version", ("key", "version")),
)


def _tenant_index(table: str) -> str:
    return f"uq_{table}_tenant_scoped"


def _system_index(table: str) -> str:
    return f"uq_{table}_system_level"


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        return
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    for table, old_name, cols in TARGETS:
        if table not in tables:
            continue
        existing = {c["name"] for c in inspector.get_unique_constraints(table)}
        if old_name in existing:
            op.drop_constraint(old_name, table, type_="unique")
        col_list = ", ".join(cols)
        op.execute(
            f"CREATE UNIQUE INDEX IF NOT EXISTS {_tenant_index(table)} "
            f"ON {table} (tenant_id, {col_list}) WHERE tenant_id IS NOT NULL"
        )
        op.execute(
            f"CREATE UNIQUE INDEX IF NOT EXISTS {_system_index(table)} "
            f"ON {table} ({col_list}) WHERE tenant_id IS NULL"
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        return
    tables = set(sa.inspect(bind).get_table_names())
    for table, old_name, cols in TARGETS:
        if table not in tables:
            continue
        col_list = ", ".join(cols)
        # Вернуть общий ключ можно, только если арендаторы не завели
        # одноимённых строк — иначе откат обрежет ровно ту работу, ради
        # которой ревизия появилась.
        #
        # Считать надо при выключенном RLS. Миграция идёт под владельцем
        # таблицы, и `FORCE ROW LEVEL SECURITY` касается его тоже: без
        # арендатора в сессии видны только системные строки, дубли арендаторов
        # не видны, проверка отвечает «чисто» — и ALTER падает уже на данных.
        # Ровно так первая редакция и обманулась: 4 строки видно, 7 всего.
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
        try:
            dupes = bind.execute(
                sa.text(
                    f"select count(*) from (select {col_list} from {table} group by {col_list} having count(*) > 1) d"
                )  # noqa: S608
            ).scalar_one()
        finally:
            op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
            op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        if dupes:
            raise RuntimeError(
                f"downgrade отменён: в {table} есть {dupes} ключей ({col_list}), "
                "занятых несколькими арендаторами. Восстанавливайтесь из дампа."
            )
        op.execute(f"DROP INDEX IF EXISTS {_tenant_index(table)}")
        op.execute(f"DROP INDEX IF EXISTS {_system_index(table)}")
        op.create_unique_constraint(old_name, table, list(cols))
