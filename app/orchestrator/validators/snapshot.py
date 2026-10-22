"""Снимок id сцен и кадров. Только чтение, без INSERT/UPDATE/DELETE.

База — та же, что у приложения: файл SQLite (``mode=ro``) или Postgres по
``DATABASE_URL``. На Postgres чтение идёт под арендатором из контекста
(RLS), поэтому снимок видит ровно кадры своего проекта.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from sqlalchemy.exc import SQLAlchemyError

from app.orchestrator.db.engine import column_names, read_rows, studio_read, table_names


class _SnapshotError(Exception):
    """Запрос вне белого списка таблиц снимка."""


def load_project_snapshot(
    project_id: int,
    *,
    conn: Any | None = None,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    """Прочитать id сцен и кадров проекта.

    Своё соединение открывается только на чтение. Чужое (SQLAlchemy или
    sqlite3) используется только для ``SELECT``. Если базы нет или таблиц нет,
    ``available`` = False и списки пустые — вызывающий решает, блокировать ли.
    """

    if conn is not None:
        return _snapshot(conn, project_id)
    if db_path is None and _on_postgres():
        with studio_read(None) as own:
            return _snapshot(own, project_id) if own is not None else _empty(False)
    path = _resolve_path(project_id, db_path)
    if path is None:
        return _empty(False)
    with studio_read(path) as own:
        return _snapshot(own, project_id) if own is not None else _empty(False)


def _on_postgres() -> bool:
    from app.settings import settings

    return settings.is_postgres


def _snapshot(conn: Any, project_id: int) -> dict[str, Any]:
    try:
        has_scenes = _table_exists(conn, "scenes")
        has_frames = _table_exists(conn, "frames")
        if not has_scenes and not has_frames:
            return _empty(False)
        scenes = _select_ids(conn, "scenes", project_id) if has_scenes else []
        frames = _select_ids(conn, "frames", project_id) if has_frames else []
    except (sqlite3.Error, SQLAlchemyError, _SnapshotError):
        return _empty(False)
    return {"scenes": scenes, "frames": frames, "available": True}


def _empty(available: bool) -> dict[str, Any]:
    return {"scenes": [], "frames": [], "available": available}


def _resolve_path(project_id: int, db_path: str | Path | None) -> Path | None:
    """Файл SQLite, где лежат кадры проекта.

    База одна на все проекты, поэтому ``project_id`` путь не меняет. В режиме
    Postgres файла нет — снимок недоступен (читать базу мимо RLS сырым sqlite3
    нельзя и нечем).
    """
    del project_id
    if db_path is not None:
        path = Path(db_path)
        return path if path.is_file() else None
    from app.settings import settings

    if settings.is_postgres:
        return None
    sqlite_path = getattr(settings, "sqlite_path", None)
    if not sqlite_path:
        return None
    path = Path(sqlite_path)
    return path if path.is_file() else None


def _table_exists(conn: Any, table: str) -> bool:
    return table in table_names(conn)


def _select_ids(conn: Any, table: str, project_id: int) -> list[dict[str, Any]]:
    if table not in {"scenes", "frames"}:
        raise _SnapshotError(f"таблица не из снимка: {table}")
    if not _table_exists(conn, table):
        return []
    if table == "frames" and _column_exists(conn, table, "attrs"):
        rows = read_rows(
            conn,
            "SELECT id, attrs FROM frames WHERE project_id = :project_id ORDER BY id",
            {"project_id": int(project_id)},
        )
        found: list[dict[str, Any]] = []
        for row in rows:
            found.append({"id": row["id"]})
            for shot_id in _kadry_ids(row["attrs"]):
                found.append({"id": shot_id})
        return found
    rows = read_rows(
        conn,
        f"SELECT id FROM {table} WHERE project_id = :project_id ORDER BY id",  # noqa: S608 — имя таблицы из белого списка
        {"project_id": int(project_id)},
    )
    return [{"id": row["id"]} for row in rows]


def _column_exists(conn: Any, table: str, column: str) -> bool:
    if table not in {"scenes", "frames"}:
        return False
    return column in column_names(conn, table)


def _kadry_ids(attrs: Any) -> list[Any]:
    if isinstance(attrs, str):
        try:
            attrs = json.loads(attrs)
        except json.JSONDecodeError:
            return []
    if not isinstance(attrs, dict):
        return []
    shots = attrs.get("кадры")
    if not isinstance(shots, list):
        return []
    found: list[Any] = []
    for shot in shots:
        if isinstance(shot, dict):
            raw = shot.get("id")
            if raw in (None, ""):
                raw = shot.get("shot_id", shot.get("frame_id"))
        elif isinstance(shot, (str, int)):
            raw = shot
        else:
            continue
        if raw in (None, ""):
            continue
        found.append(raw if isinstance(raw, int) else str(raw))
    return found
