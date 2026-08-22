"""Приведение схемы к head — единственная точка входа для миграций.

До этого схема жила в трёх местах сразу: `Base.metadata.create_all` на
старте, ad-hoc `ALTER TABLE` в `app/main.py::_init_db` и ещё один блок в
`app/services/db_v2.migrate_db_v2_schema`. create_all не умеет ALTER, так
что изменение существующей таблицы молча не доезжало до баз, поднятых
раньше; alembic лежал в зависимостях, но не использовался (`docs/
TECH_DEBT_PLAN.md` п.11).

Три сценария, различаются по состоянию базы:

* **пусто** (файла нет либо ни одной таблицы) — прогоняем `upgrade head`;
  baseline (0001) создаёт схему из моделей, дальнейшие ревизии применяются
  поверх;
* **унаследованная** (таблицы есть, `alembic_version` нет) — база заказчика,
  которую делал create_all. Её нельзя прогонять с нуля: 0001 попытался бы
  создать существующие таблицы. Ставим `stamp 0001` и догоняем
  `upgrade head` — то есть выполняются только 0002+, ровно те идемпотентные
  ALTER'ы, что раньше делал `_init_db`;
* **управляемая** — обычный `upgrade head`.

Функция идемпотентна и безопасна для повторного вызова: её дёргают и
`app.main`, и lifespan FastAPI (dev-режим с `--reload` поднимается без
`app.main`).
"""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from loguru import logger
from sqlalchemy import inspect
from sqlalchemy.engine import Connection

from app.project_root import find_project_root

BASELINE_REVISION = "0001"


def alembic_config(connection: Connection | None = None) -> Config:
    root = find_project_root()
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "migrations"))
    if connection is not None:
        cfg.attributes["connection"] = connection
    return cfg


def _state(connection: Connection) -> tuple[bool, bool]:
    """(есть ли пользовательские таблицы, под управлением ли alembic)."""
    inspector = inspect(connection)
    tables = set(inspector.get_table_names())
    managed = "alembic_version" in tables
    user_tables = tables - {"alembic_version"}
    return bool(user_tables), managed


def _upgrade_sync(connection: Connection) -> None:
    has_tables, managed = _state(connection)
    cfg = alembic_config(connection)

    if managed:
        current = MigrationContext.configure(connection).get_current_revision()
        logger.info("migrations: alembic head, текущая ревизия {}", current)
        command.upgrade(cfg, "head")
        return

    if has_tables:
        # База заказчика, созданная create_all: схема есть, истории нет.
        logger.info(
            "migrations: унаследованная база без alembic_version — stamp {} + upgrade head",
            BASELINE_REVISION,
        )
        command.stamp(cfg, BASELINE_REVISION)
        command.upgrade(cfg, "head")
        return

    logger.info("migrations: пустая база — upgrade head с baseline")
    command.upgrade(cfg, "head")


async def upgrade_to_head() -> None:
    """Догнать схему до head. Ошибка миграции — фатальна, не глушим."""
    from app.db import engine

    async with engine.begin() as conn:
        await conn.run_sync(_upgrade_sync)


def upgrade_to_head_sync(sqlite_path: Path | None = None) -> None:
    """Синхронный вариант для скриптов/CLI (не из event loop)."""
    from sqlalchemy import create_engine

    from app.settings import settings

    path = Path(sqlite_path) if sqlite_path else Path(settings.sqlite_path)
    engine = create_engine(f"sqlite+pysqlite:///{path}", future=True)
    try:
        with engine.begin() as conn:
            _upgrade_sync(conn)
    finally:
        engine.dispose()
