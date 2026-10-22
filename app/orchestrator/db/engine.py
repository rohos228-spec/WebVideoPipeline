"""Синхронный движок SQLAlchemy для таблиц оркестратора и прямых читателей базы.

Движок пришёл из линии, где база была только SQLite, и ходил в неё сырым
``sqlite3.connect`` по ``settings.sqlite_path``. В WebVideoPipeline база
выбирается по ``DATABASE_URL`` (``app/db.py``): на Postgres файла нет, и такой
код молча писал бы в пустой локальный файл, а читал бы из него же. Здесь —
один вход для синхронного кода движка (события чата, задачи, сообщения,
уведомления, снимки): тот же сервер, что у приложения, другой драйвер.

* **SQLite** — ``NullPool`` и те же PRAGMA, что были у сырого соединения:
  ``foreign_keys``, ``busy_timeout`` (с подменой на время ``busy_timeout()``),
  WAL. Каждое ``connect()`` — новое соединение sqlite3, как раньше. Драйвер
  ``pysqlite`` оставляет неявный ``BEGIN`` модулю sqlite3, так что границы
  транзакций не поменялись.
* **Postgres** — синхронный ``psycopg`` к тому же серверу (асинхронный
  ``asyncpg`` у приложения в event loop, а этот код живёт в рабочих потоках).
  Пул небольшой: пишут сюда события чата и задачи, не шаги пайплайна.
  Каждая транзакция сообщает арендатора через ``set_config(..., true)`` —
  ровно как ``after_begin`` у сессий приложения: читатели кадров и сцен на
  Postgres упираются в RLS, и без привязки видели бы пустоту.

Явный путь (``path``) — всегда файл SQLite: им пользуются инструменты и
тесты, которые чинят или читают конкретную базу.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.engine import Connection
from sqlalchemy.pool import NullPool

from app.settings import settings

BUSY_TIMEOUT_MS = 5000
_BUSY_MS: ContextVar[int | None] = ContextVar("orch_busy_ms", default=None)

# Движков немного: один на Postgres, по одному на файл SQLite. Тесты меняют
# ``sqlite_path`` на каждый тест — без предела словарь рос бы на всю суиту.
_ENGINE_CAP = 16
_ENGINES: OrderedDict[str, Engine] = OrderedDict()
_ENGINES_LOCK = threading.Lock()

_BIND_TENANT_SQL = text("select set_config('app.tenant_id', :tid, true)")


def resolve_db_path(path: Path | None = None) -> Path:
    """Абсолютный путь SQLite. ``None`` — ``settings.sqlite_path`` в момент вызова."""
    db_path = Path(path) if path is not None else Path(settings.sqlite_path)
    if not db_path.is_absolute():
        db_path = Path.cwd() / db_path
    return db_path


def uses_postgres(path: Path | None = None) -> bool:
    """Цель — Postgres приложения. Явный путь всегда означает файл SQLite."""
    return path is None and settings.is_postgres


def sync_postgres_url() -> str:
    """URL приложения с синхронным драйвером. Тот же, что у alembic-раннера."""
    url = settings.db_url
    return url.replace("+asyncpg", "+psycopg").replace("+psycopg_async", "+psycopg")


def target_url(path: Path | None = None) -> str:
    if uses_postgres(path):
        return sync_postgres_url()
    return f"sqlite+pysqlite:///{resolve_db_path(path).as_posix()}"


def target_key(path: Path | None = None) -> str:
    """Ключ базы для кэшей «один раз на базу»: путь файла или URL Postgres."""
    if uses_postgres(path):
        return sync_postgres_url()
    return str(resolve_db_path(path))


@contextmanager
def busy_timeout(ms: int) -> Iterator[None]:
    """Короткий ``busy_timeout`` для соединений SQLite, открытых в этом контексте."""
    token = _BUSY_MS.set(max(0, int(ms)))
    try:
        yield
    finally:
        _BUSY_MS.reset(token)


def _sqlite_pragmas(dbapi_connection, _record) -> None:
    busy = _BUSY_MS.get()
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS if busy is None else busy}")
        cursor.execute("PRAGMA journal_mode=WAL").fetchone()
    finally:
        cursor.close()


def _bind_tenant(conn: Connection) -> None:
    """Транзакция на Postgres сообщает арендатора, как сессии ``app/db.py``."""
    from app.services.tenant import current_tenant

    tenant_id = current_tenant()
    if tenant_id is None:
        return
    conn.execute(_BIND_TENANT_SQL, {"tid": tenant_id})


def _make_engine(url: str) -> Engine:
    if url.startswith("sqlite"):
        engine = create_engine(
            url,
            future=True,
            poolclass=NullPool,
            # Долгоживущее соединение шины событий ходит из пула потоков.
            connect_args={"check_same_thread": False},
        )
        event.listen(engine, "connect", _sqlite_pragmas)
        return engine
    engine = create_engine(
        url,
        future=True,
        pool_size=4,
        max_overflow=4,
        pool_pre_ping=True,
        pool_recycle=1800,
    )
    event.listen(engine, "begin", _bind_tenant)
    return engine


def get_engine(path: Path | None = None) -> Engine:
    url = target_url(path)
    with _ENGINES_LOCK:
        engine = _ENGINES.get(url)
        if engine is not None:
            _ENGINES.move_to_end(url)
            return engine
        if url.startswith("sqlite"):
            resolve_db_path(path).parent.mkdir(parents=True, exist_ok=True)
        engine = _make_engine(url)
        _ENGINES[url] = engine
        while len(_ENGINES) > _ENGINE_CAP:
            _old_url, old = _ENGINES.popitem(last=False)
            old.dispose()
        return engine


def dispose_engines() -> None:
    """Закрыть пулы (тесты, смена ``DATABASE_URL`` в процессе)."""
    with _ENGINES_LOCK:
        engines = list(_ENGINES.values())
        _ENGINES.clear()
    for engine in engines:
        engine.dispose()


def readonly_sqlite_engine(path: Path) -> Engine:
    """Движок только на чтение к конкретному файлу (``mode=ro``): снимки, индексация."""
    uri = f"{Path(path).resolve().as_uri()}?mode=ro"
    return create_engine(
        "sqlite+pysqlite:///",
        future=True,
        poolclass=NullPool,
        creator=lambda: _sqlite_ro_connect(uri),
    )


def _sqlite_ro_connect(uri: str):
    import sqlite3

    return sqlite3.connect(uri, uri=True, timeout=5, check_same_thread=False)


@contextmanager
def studio_read(path: Path | None = None) -> Iterator[Connection | None]:
    """Соединение для чтения базы студии.

    ``path is None`` — база приложения: Postgres по ``DATABASE_URL`` или файл
    ``settings.sqlite_path``. Файла SQLite нет — ``None``: читать нечего,
    и создавать пустую базу ради чтения нельзя.
    """
    if uses_postgres(path):
        with get_engine().connect() as conn:
            yield conn
        return
    db_path = resolve_db_path(path)
    if not db_path.is_file():
        yield None
        return
    engine = readonly_sqlite_engine(db_path)
    try:
        with engine.connect() as conn:
            yield conn
    finally:
        engine.dispose()


def is_locked(exc: BaseException) -> bool:
    """«База занята» SQLite — и сырой, и завёрнутый SQLAlchemy."""
    import sqlite3

    from sqlalchemy.exc import OperationalError

    if isinstance(exc, OperationalError):
        exc = exc.orig if exc.orig is not None else exc
    if not isinstance(exc, (sqlite3.OperationalError, OperationalError)):
        return False
    message = str(exc).lower()
    return "locked" in message or "busy" in message


class _AppDatabase:
    """Маркер «база приложения целиком»: у Postgres нет пути к файлу.

    Читатели, которым исторически передавали путь ``state.db`` (индексация
    истории и нод для RAG), получают этот маркер и открывают ту же базу, что
    приложение, — Postgres или файл ``settings.sqlite_path``.
    """

    def __repr__(self) -> str:
        return "APP_DATABASE"


APP_DATABASE = _AppDatabase()


@contextmanager
def open_read(source: Path | _AppDatabase | None) -> Iterator[Connection | None]:
    """Соединение на чтение по пути файла SQLite или маркеру ``APP_DATABASE``."""
    if source is None:
        yield None
        return
    with studio_read(None if source is APP_DATABASE else Path(source)) as conn:  # type: ignore[arg-type]
        yield conn


# Читатели ниже принимают и соединение SQLAlchemy, и сырое sqlite3 (его
# передают инструменты и тесты). SQL пишется с именованными параметрами
# ``:name`` — их понимают оба.


def read_rows(conn, sql: str, params: dict | None = None) -> list[dict]:
    if isinstance(conn, Connection):
        return [dict(row) for row in conn.execute(text(sql), params or {}).mappings()]
    cursor = conn.execute(sql, params or {})
    names = [item[0] for item in cursor.description or ()]
    return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]


def table_names(conn) -> set[str]:
    if isinstance(conn, Connection):
        from sqlalchemy import inspect

        return set(inspect(conn).get_table_names())
    return {str(row[0]) for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def column_names(conn, table: str) -> set[str]:
    if isinstance(conn, Connection):
        from sqlalchemy import inspect
        from sqlalchemy.exc import NoSuchTableError

        try:
            return {str(column["name"]) for column in inspect(conn).get_columns(table)}
        except NoSuchTableError:
            return set()
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")}
