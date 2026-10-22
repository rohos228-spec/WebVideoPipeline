"""Фабрика сессий для чтения кадров проекта.

Движок (линия video-pipeline int/chat-v1) выбирал между общей базой и
отдельным ``project.db`` проекта и проверял коллизии id между ними. В
WebVideoPipeline база одна (``app/db.py``: SQLite владельца или Postgres с
RLS), поэтому здесь всегда отдаётся общая фабрика сессий. Синхронизацию
двух баз сюда не переносить.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


async def session_factory_for(project_id: int, data_dir: Path | str | None = None) -> Any:
    """Одна база: всегда ``app.db.SessionLocal``. Аргументы — ради совместимости."""
    from app import db as app_db

    del project_id, data_dir
    return app_db.SessionLocal
