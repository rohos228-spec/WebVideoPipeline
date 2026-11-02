"""Фоновый воркер пайплайна — один на процесс, стартует из app.main и web lifespan."""

from __future__ import annotations

import asyncio

from loguru import logger

_worker_task: asyncio.Task | None = None
_worker_wake_event: asyncio.Event | None = None


def get_worker_wake_event() -> asyncio.Event:
    global _worker_wake_event
    if _worker_wake_event is None:
        _worker_wake_event = asyncio.Event()
    return _worker_wake_event


def wake_worker() -> None:
    """Wake the background worker loop immediately without waiting for sleep timeout."""
    try:
        get_worker_wake_event().set()
    except Exception:
        pass


def ensure_pipeline_worker_started(bot) -> asyncio.Task:
    """Поднимает `_run_worker_loop`, если ещё не запущен."""
    global _worker_task
    if _worker_task is not None and not _worker_task.done():
        return _worker_task
    from app.main import _run_worker_loop

    _worker_task = asyncio.create_task(_run_worker_loop(bot))
    logger.info("pipeline_worker: background worker started")
    return _worker_task


def pipeline_worker_running() -> bool:
    return _worker_task is not None and not _worker_task.done()


def get_pipeline_worker_task() -> asyncio.Task | None:
    return _worker_task
