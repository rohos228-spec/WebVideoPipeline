"""Совместимость с «базой проекта» движка поверх ОДНОЙ базы WebVideoPipeline.

Движок (линия video-pipeline int/chat-v1) держал у каждого проекта свой файл
`project.db` и синхронизировал его с общей `state.db`. В WebVideoPipeline база
одна: SQLite в режиме владельца или Postgres с RLS в режиме SaaS (`app/db.py`).
Перенесённый код движка по-прежнему зовёт `app.project_db`, поэтому здесь
оставлены те же имена, но все они ведут в единую базу:

* сессии — это `app.db.SessionLocal` / `app.db.session_scope`; арендатор
  привязывается событиями сессии (`after_begin` / `before_flush` в `app/db.py`),
  отдельно ничего проставлять не нужно;
* «путь к базе проекта» — файл единой SQLite-базы (`settings.sqlite_path`),
  а в режиме Postgres файла нет;
* синхронизация двух баз (pull/push/sync/replica) — no-op: одна база, копировать
  некуда. Функции оставлены, чтобы перенесённые вызовы не падали на импорте.

Синхронизацию и пер-проектные движки сюда НЕ переносить: это отдельная
архитектура, от которой новая линия сознательно ушла.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db import SessionLocal, session_scope
from app.models import Project
from app.settings import settings

_PROJECT_DIR_CACHE: dict[int, Path] = {}


def worker_use_project_db() -> bool:
    """Отдельной базы проекта нет — одна база. Всегда False."""
    return False


def _single_sqlite_file() -> Path | None:
    """Файл единой SQLite-базы; None в режиме Postgres (там файла нет)."""
    if settings.is_postgres:
        return None
    sp = getattr(settings, "sqlite_path", None)
    return Path(sp) if sp else None


def resolve_project_db_path(project_data_dir: Path | str) -> Path:
    """Путь к базе, где лежат данные проекта: одна база для всех проектов.

    `project_data_dir` принимается ради совместимости и не используется.
    В режиме Postgres возвращается путь `settings.sqlite_path`, который этим
    режимом не используется; вызывающим, кому нужна именно «отдельная база
    проекта», надо смотреть `worker_use_project_db()` (всегда False).
    """
    del project_data_dir
    return Path(settings.sqlite_path)


def resolve_project_db_path_by_id(project_id: int) -> Path | None:
    """Синхронный путь к SQLite-файлу с данными проекта (для чекеров/снимков).

    Одна база: возвращается файл единой SQLite-базы, если он есть. В режиме
    Postgres — None: читать базу мимо RLS сырым sqlite3 нельзя и нечего.
    """
    del project_id
    path = _single_sqlite_file()
    if path is None or not path.is_file():
        return None
    return path


def register_project_data_dir(project_id: int, data_dir: Path | str) -> None:
    _PROJECT_DIR_CACHE[int(project_id)] = Path(data_dir)


def unregister_project_data_dir(project_id: int) -> None:
    _PROJECT_DIR_CACHE.pop(int(project_id), None)


async def get_project_data_dir(project_id: int, master_session: AsyncSession | None = None) -> Path | None:
    """Папка проекта (`projects.data_dir`) из единой базы."""
    pid = int(project_id)
    if pid in _PROJECT_DIR_CACHE:
        return _PROJECT_DIR_CACHE[pid]
    if master_session is not None:
        row = await master_session.get(Project, pid)
    else:
        async with SessionLocal() as s:
            row = await s.get(Project, pid)
    if row is None or not row.data_dir:
        return None
    path = Path(row.data_dir)
    _PROJECT_DIR_CACHE[pid] = path
    return path


async def get_project_sessionmaker(
    project_data_dir: Path | str | None = None,
) -> async_sessionmaker[AsyncSession]:
    """Фабрика сессий — единая `SessionLocal` (арендатор привязывается событием сессии)."""
    del project_data_dir
    return SessionLocal


async def get_project_engine(project_data_dir: Path | str | None = None) -> Any:
    """Движок единой базы."""
    del project_data_dir
    from app.db import engine

    return engine


@asynccontextmanager
async def project_session_scope(project_data_dir: Path | str | None = None) -> AsyncIterator[AsyncSession]:
    """Сессия единой базы с commit/rollback (= `app.db.session_scope`)."""
    del project_data_dir
    async with session_scope() as s:
        yield s


@asynccontextmanager
async def project_db_session_scope(project_id_or_dir: int | Path | str) -> AsyncIterator[AsyncSession]:
    """Сессия для проекта по id или папке: одна база, тот же `session_scope`."""
    del project_id_or_dir
    async with session_scope() as s:
        yield s


async def get_project_db_session(project_id: int) -> AsyncIterator[AsyncSession]:
    """Зависимость FastAPI: сессия единой базы; 404, если проекта нет."""
    async with SessionLocal() as session:
        row = await session.get(Project, int(project_id))
        if row is None:
            from fastapi import HTTPException

            raise HTTPException(status_code=404, detail=f"проект {project_id} не найден")
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


async def init_project_db(project_data_dir: Path | str, project: Project | None = None) -> None:
    """No-op: одна база, схему создаёт `app.db_migrations` при старте."""
    del project_data_dir, project


async def close_all_project_engines() -> None:
    """No-op: пер-проектных движков нет, одна база. Чистит только кэш папок."""
    _PROJECT_DIR_CACHE.clear()


async def auto_migrate_legacy_projects_if_needed() -> int:
    """No-op: одна база, переносить данные в project.db не нужно."""
    return 0


# --- Синхронизация двух баз: одна база, поэтому всё ниже — no-op. ---------


async def pull_master_runtime_into_project(
    session: AsyncSession,
    project: Project,
    *,
    copy_noderuns: bool = False,
) -> bool:
    """No-op: одна база — тянуть runtime из state.db в project.db некуда."""
    del session, project, copy_noderuns
    return False


async def push_runtime_to_master(session: AsyncSession, project: Project) -> bool:
    """No-op: одна база — пушить runtime в master нечего, это успех."""
    del session, project
    return True


async def push_runtime_to_master_recorded(session: AsyncSession, project: Project) -> bool:
    """No-op: одна база."""
    del session, project
    return True


async def push_runtime_to_project_db(session: AsyncSession, project: Project) -> bool:
    """No-op: одна база."""
    del session, project
    return True


async def push_display_fields_to_master(session: AsyncSession, project: Project, keys) -> None:
    """No-op: одна база."""
    del session, project, keys


async def sync_runtime_both_ways(session: AsyncSession, project: Project) -> None:
    """No-op: одна база."""
    del session, project


async def sync_project_row_to_master_db(project: Project) -> None:
    """No-op: одна база."""
    del project


async def sync_project_row_to_project_db(project: Project, data_dir: Path | str | None = None) -> None:
    """No-op: одна база."""
    del project, data_dir


async def run_on_replica_project(session: AsyncSession, project: Project, fn) -> None:
    """No-op: реплики нет, одна база (основную запись делает вызывающий в своей сессии)."""
    del session, project, fn
