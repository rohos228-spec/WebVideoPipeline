"""Схема таблиц оркестратора и короткие соединения к ним.

Схема ``orch_*`` / ``node_*`` описана пронумерованными SQL-файлами рядом с
этим модулем (диалект SQLite). Где она применяется:

* **alembic** (ревизия ``0018_orch_tables``) — основной путь. На SQLite
  ревизия выполняет эти же файлы по одному оператору и пишет их в журнал
  ``orch_schema_migrations``; на Postgres — свою DDL того же смысла.
* **этот раннер** — только SQLite и только для баз, которые alembic не
  проходили (тестовые файлы, базы инструментов). Повторный запуск безопасен:
  имя файла пишется в ``orch_schema_migrations``, ``ADD COLUMN`` для уже
  существующей колонки пропускается.

На Postgres раннер ничего не создаёт: схема сервера меняется только
миграциями. Если таблиц нет — это ошибка развёртывания, и о ней говорится
сразу, а не первой упавшей вставкой события.
"""

from __future__ import annotations

import re
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.engine import Connection

from app.orchestrator.db.engine import (
    BUSY_TIMEOUT_MS,
    busy_timeout,
    get_engine,
    is_locked,
    resolve_db_path,
    target_key,
    uses_postgres,
)

__all__ = [
    "BUSY_TIMEOUT_MS",
    "ORCH_SQL_FILES",
    "ORCH_TABLES",
    "apply_migrations",
    "busy_timeout",
    "connect",
    "ensure_migrated",
    "is_locked",
    "node_key_ok",
    "resolve_db_path",
    "sqlite_statements",
    "target_key",
    "utc_now",
]

_MIGRATIONS_DIR = Path(__file__).resolve().parent
_NODE_KEY_RE = re.compile(r"^n_[a-z0-9_]+$")
_ADD_COLUMN_RE = re.compile(
    r"^[ \t]*ALTER\s+TABLE\s+(\w+)\s+ADD\s+COLUMN\s+(\w+)\b[^;]*;",
    re.IGNORECASE | re.MULTILINE,
)
_MIGRATED: set[str] = set()
_MIGRATE_LOCK = threading.Lock()

#: Файлы, которые накатывает ревизия alembic 0018. Новый файл схемы —
#: новая ревизия alembic, этот список не дописывается.
ORCH_SQL_FILES: tuple[str, ...] = (
    "001_node_defs.sql",
    "002_node_prompt_versions.sql",
    "003_orch_reports.sql",
    "004_orch_tasks.sql",
    "005_orch_checkpoints.sql",
    "006_orch_events.sql",
    "007_orch_messages.sql",
    "008_orch_llm_calls.sql",
    "009_orch_tasks_project.sql",
)

#: Таблицы, без которых движок на Postgres не стартует.
ORCH_TABLES: tuple[str, ...] = (
    "node_defs",
    "node_prompt_versions",
    "orch_reports",
    "orch_report_versions",
    "orch_tasks",
    "orch_steps",
    "orch_checkpoints",
    "orch_events",
    "orch_event_seq",
    "orch_messages",
    "orch_llm_calls",
)

JOURNAL_DDL = """
CREATE TABLE IF NOT EXISTS orch_schema_migrations (
    name TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL
)
"""


def utc_now() -> str:
    """ISO-строка UTC, лексикографически сравнимая."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def ensure_migrated(path: Path | None = None) -> str:
    """Подготовить схему один раз на процесс для этой базы. Возвращает ключ базы.

    Повторный вызов с той же базой не открывает каталог SQL и не читает журнал.
    Старт процесса (``build_runtime``) вызывает это до горячего пути событий.
    """
    key = target_key(path)
    if key in _MIGRATED:
        return key
    with _MIGRATE_LOCK:
        if key in _MIGRATED:
            return key
        engine = get_engine(path)
        if uses_postgres(path):
            _require_tables(engine)
        else:
            raw = engine.raw_connection()
            try:
                apply_migrations(raw.driver_connection)
            finally:
                raw.close()
        _MIGRATED.add(key)
    return key


def _require_tables(engine: Any) -> None:
    from sqlalchemy import inspect

    with engine.connect() as conn:
        present = set(inspect(conn).get_table_names())
    missing = [name for name in ORCH_TABLES if name not in present]
    if missing:
        raise RuntimeError(
            "в базе Postgres нет таблиц оркестратора ("
            + ", ".join(missing)
            + "): выполните `alembic upgrade head`"
        )


def connect(path: Path | None = None) -> Connection:
    """Короткое соединение SQLAlchemy. Схема готовится ``ensure_migrated``.

    Запросы — ``text()`` с именованными параметрами, строки — ``.mappings()``.
    Горячий путь событий держит отдельное долгоживущее соединение, не это.
    """
    ensure_migrated(path)
    return get_engine(path).connect()


def apply_migrations(conn: Any) -> list[str]:
    """Применить ещё не записанные ``*.sql`` (SQLite, соединение sqlite3)."""
    conn.execute(JOURNAL_DDL)
    applied = {row[0] for row in conn.execute("SELECT name FROM orch_schema_migrations")}
    done: list[str] = []
    for path in sorted(_MIGRATIONS_DIR.glob("*.sql")):
        if path.name in applied:
            continue
        script = _without_existing_columns(
            lambda table, column: _has_column(conn, table, column),
            path.read_text(encoding="utf-8"),
        )
        if script.strip():
            conn.executescript(script)
        conn.execute(
            "INSERT INTO orch_schema_migrations (name, applied_at) VALUES (?, ?)",
            (path.name, utc_now()),
        )
        conn.commit()
        done.append(path.name)
    return done


def sqlite_statements(name: str, has_column: Any) -> list[str]:
    """Операторы файла схемы по одному — для исполнителей без ``executescript``.

    Триггер с ``BEGIN … END`` содержит ``;`` внутри, поэтому граница
    оператора — ``sqlite3.complete_statement``, а не разбиение по ``;``.
    """
    import sqlite3

    script = _without_existing_columns(has_column, (_MIGRATIONS_DIR / name).read_text(encoding="utf-8"))
    statements: list[str] = []
    buffer = ""
    for line in script.splitlines(keepends=True):
        if not buffer and (not line.strip() or line.lstrip().startswith("--")):
            continue
        buffer += line
        if sqlite3.complete_statement(buffer):
            statements.append(buffer.strip())
            buffer = ""
    if buffer.strip():
        raise ValueError(f"{name}: незавершённый оператор в конце файла")
    return statements


def _has_column(conn: Any, table: str, column: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(row[1] == column for row in rows)


def _without_existing_columns(has_column: Any, sql: str) -> str:
    """Повторный ALTER после обрыва между файлом и журналом не дублирует колонку."""

    def skip(match: re.Match[str]) -> str:
        if has_column(match.group(1), match.group(2)):
            return ""
        return match.group(0)

    return _ADD_COLUMN_RE.sub(skip, sql)


def node_key_ok(value: str) -> bool:
    return _NODE_KEY_RE.fullmatch(value or "") is not None
