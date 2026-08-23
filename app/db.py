"""Движок БД и фабрика сессий. Диалект выбирается по `DATABASE_URL`.

Два режима, и различаются они не косметикой:

* **SQLite** — режим владельца, одна машина, один файл. Пула нет: единственный
  writer, `NullPool` и WAL. `QueuePool` здесь давал `database is locked` при
  одновременных PATCH канваса и записи воркера.
* **Postgres** — режим SaaS. Есть пул, есть row-level security, и каждая
  транзакция обязана сообщить, от чьего имени она идёт: политика на таблице
  читает `app.tenant_id`, а выставляет его `SET LOCAL` в начале транзакции
  (`docs/SAAS-PIVOT.md` §4.1).

**`SET LOCAL`, а не `SET`.** Настройка живёт до конца транзакции и умирает
вместе с ней. Соединение возвращается в пул чистым — иначе следующий
арендатор, взявший то же соединение, унаследовал бы чужую личность, и RLS
пропустил бы его к чужим данным. Это ровно тот класс ошибки, ради защиты от
которого RLS и выбран.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.settings import settings


def _make_engine():
    if settings.is_postgres:
        # pool_pre_ping: соединение могло умереть, пока шаг ждал провайдера
        # минутами. Дешевле проверить, чем уронить шаг после генерации.
        return create_async_engine(
            settings.db_url,
            echo=False,
            future=True,
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow,
            pool_pre_ping=True,
            pool_recycle=1800,
        )

    # Гарантируем, что папка под БД существует — SQLite сам файл создаст, а
    # вот родительскую директорию нужно создать заранее.
    _db_path = settings.sqlite_path
    if not _db_path.is_absolute():
        from pathlib import Path

        _db_path = Path.cwd() / _db_path
    _db_path.parent.mkdir(parents=True, exist_ok=True)

    return create_async_engine(
        settings.db_url,
        echo=False,
        future=True,
        poolclass=NullPool,
        # Montage / canvas save: запас против busy writer (worker Outsee).
        connect_args={"timeout": 60},
    )


engine = _make_engine()


def _configure_sqlite_connection(dbapi_connection, _connection_record) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=60000")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.close()


if not settings.is_postgres:
    event.listens_for(engine.sync_engine, "connect")(_configure_sqlite_connection)

SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def bind_tenant(session: AsyncSession) -> str | None:
    """Сообщить транзакции, от чьего имени она идёт. Возвращает арендатора.

    На SQLite не делает ничего: RLS там нет, а многоарендный режим на нём
    запрещён явной проверкой (`tenant.require_isolation`).
    """
    from app.services.tenant import current_tenant, require_isolation

    require_isolation()
    tenant_id = current_tenant()
    if tenant_id is None or not settings.is_postgres:
        return tenant_id
    # tenant_id уже проверен как UUID в `tenant._normalize`, но параметр всё
    # равно передаётся связыванием: `SET LOCAL` не принимает плейсхолдер,
    # поэтому идём через `set_config`, который принимает.
    await session.execute(
        text("select set_config('app.tenant_id', :tid, true)"),
        {"tid": tenant_id},
    )
    return tenant_id


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        try:
            await bind_tenant(session)
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
