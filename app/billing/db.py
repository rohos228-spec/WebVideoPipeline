"""SQLite биллинга (отдельный файл) + применение миграций billing_*.sql."""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from app import settings_billing

_MIGRATIONS = Path(__file__).resolve().parent / "migrations"
_lock = threading.RLock()
_migrated: set[str] = set()


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def migrate(conn: sqlite3.Connection) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS billing_migrations (name TEXT PRIMARY KEY, applied_at TEXT)")
    done = {r[0] for r in conn.execute("SELECT name FROM billing_migrations")}
    for f in sorted(_MIGRATIONS.glob("billing_*.sql")):
        if f.name in done:
            continue
        conn.executescript(f.read_text(encoding="utf-8"))
        conn.execute("INSERT INTO billing_migrations VALUES (?, ?)", (f.name, now_iso()))


@contextmanager
def connect():
    path = settings_billing.load().db_path
    conn = _connect(path)
    try:
        key = str(path.resolve())
        if key not in _migrated:
            with _lock:
                migrate(conn)
                _migrated.add(key)
        yield conn
    finally:
        conn.close()


@contextmanager
def tx():
    """Транзакция BEGIN IMMEDIATE — сериализует писателей."""
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
