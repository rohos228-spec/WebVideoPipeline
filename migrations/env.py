"""Alembic env: async-движок проекта, URL из настроек.

URL берётся из `app.settings.settings.db_url`, а не из alembic.ini —
`SQLITE_PATH` переопределяется в `.env` и в тестах, и захардкоженный в ini
адрес мигрировал бы не ту базу.

`render_as_batch=True` обязателен: SQLite не умеет ALTER COLUMN / DROP
COLUMN, alembic обходит это пересозданием таблицы (batch mode).
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.models import Base
from app.settings import settings

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _url() -> str:
    return settings.db_url


def run_migrations_offline() -> None:
    context.configure(
        url=_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=True,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def _run_async_migrations() -> None:
    engine = create_async_engine(_url(), poolclass=NullPool, future=True)
    try:
        async with engine.connect() as connection:
            await connection.run_sync(_do_run_migrations)
            await connection.commit()
    finally:
        await engine.dispose()


def run_migrations_online() -> None:
    # Если alembic вызван из уже работающего event loop (наш runner делает так),
    # соединение передаётся снаружи через config.attributes.
    connectable = config.attributes.get("connection", None)
    if connectable is not None:
        _do_run_migrations(connectable)
        return
    asyncio.run(_run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
