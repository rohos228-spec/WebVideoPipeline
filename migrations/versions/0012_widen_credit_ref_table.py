"""credit_entries.ref_table: 20 → 40 символов

Revision ID: 0012
Revises: 0011
Create Date: 2026-08-25

Код пишет в эту колонку `llm_calls+media_calls` — 21 символ при объявленных
двадцати. На SQLite это проходит: длину `VARCHAR` он не проверяет вообще. На
Postgres — отказ:

    asyncpg.exceptions.StringDataRightTruncationError:
    value too long for type character varying(20)

Отказ приходится на вставку проводки бесплатного уровня, то есть на КАЖДЫЙ
первый шаг нового проекта. Шаг падает, `step_failure_policy` считает это
ошибкой конвейера и через три попытки уводит проект в паузу на тридцать минут.
Снаружи это выглядит как «нажал «Сгенерировать» — ничего не произошло».

Поймано первым живым прогоном на боевом Postgres. Вся суита при этом зелёная:
она гоняется на SQLite, где ограничение не действует.

**Почему 40, а не 21.** Значение собирается из имён таблиц через `+`, и
следующая пара имён будет длиннее. Двадцать одно означало бы чинить это
второй раз; сорок закрывает обозримое.

**Данные не теряются.** Расширение `VARCHAR` — операция только над каталогом:
Postgres не переписывает таблицу и не блокирует её надолго.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "credit_entries"
COLUMN = "ref_table"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if TABLE not in set(inspector.get_table_names()):
        # Как в 0007–0011: на пустой базе схему создаёт baseline из моделей,
        # и там колонка уже нужной ширины.
        return

    # SQLite не умеет ALTER COLUMN TYPE и в ограничение всё равно не упирается
    # — менять там нечего.
    if bind.dialect.name == "sqlite":
        return

    op.alter_column(
        TABLE,
        COLUMN,
        existing_type=sa.String(length=20),
        type_=sa.String(length=40),
        existing_nullable=False,
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        return
    if TABLE not in set(sa.inspect(bind).get_table_names()):
        return

    # Сужение обрежет уже записанные значения длиннее двадцати — а они там
    # ровно те, ради которых ревизия и появилась. Отказываем громко, а не
    # портим данные молча: `downgrade` в этом проекте и так не путь отката,
    # им служит дамп перед выкладкой (deploy/studio/deploy.sh).
    too_long = bind.execute(
        sa.text(f"select count(*) from {TABLE} where length({COLUMN}) > 20")  # noqa: S608
    ).scalar_one()
    if too_long:
        raise RuntimeError(
            f"downgrade отменён: в {TABLE}.{COLUMN} есть {too_long} значений длиннее 20 символов, "
            "сужение колонки их обрежет. Восстанавливайтесь из дампа."
        )

    op.alter_column(
        TABLE,
        COLUMN,
        existing_type=sa.String(length=40),
        type_=sa.String(length=20),
        existing_nullable=False,
    )
