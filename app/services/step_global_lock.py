"""Кросс-проектные блокировки шагов (SQLite + параллельные проекты).

Иначе WORKER_MAX_PARALLEL>1: N проектов одновременно в split/img_pr/…
пишут xlsx/DB → `sqlite3.OperationalError: database is locked` + PendingRollback.
"""

from __future__ import annotations

import asyncio
from typing import Any

from loguru import logger

# Шаг, где три параллельных проекта уже ловили database is locked (split frames).
_SPLIT_LOCK = asyncio.Lock()

# Коды шагов, которые нельзя гонять одновременно между проектами.
STEP_LOCK_CODES = frozenset({"split"})

# Разумный TTL в секундах для аренды шага разбивки (вместо 1 часа).
# Шаг split занимает секунды; 120 секунд с запасом хватает, а при сбое процесс
# не блокирует систему на час.
SPLIT_LEASE_TTL_S = 120
SPLIT_RENEW_INTERVAL_S = 30


def step_code_from_status(status) -> str | None:
    """ProjectStatus.running → код шага для lock (если задан)."""
    if status is None:
        return None
    if status.name == "splitting":
        return "split"
    return None


def force_unlock_split_lock() -> bool:
    """Аварийно сбросить in-memory замок _SPLIT_LOCK, если он завис."""
    released = False
    while _SPLIT_LOCK.locked():
        try:
            _SPLIT_LOCK.release()
            released = True
        except RuntimeError:
            break
    if released:
        logger.warning("step_global_lock: принудительно сброшен зависший _SPLIT_LOCK")
    return released


class StepGlobalLock:
    def __init__(self, code: str | None, session: Any | None = None) -> None:
        self.code = code
        self.session = session
        self._renewer: asyncio.Task | None = None
        self._owner: str | None = None
        self._acquired = False
        self._split_locked = False

    async def __aenter__(self) -> StepGlobalLock:
        if self.code == "split":
            await _SPLIT_LOCK.acquire()
            self._split_locked = True
            try:
                from app.services.work_lease import (
                    acquire,
                    current_owner,
                    renew,
                )

                self._owner = current_owner()
                while not await acquire(0, "step:split", owner=self._owner, ttl_s=SPLIT_LEASE_TTL_S):
                    await asyncio.sleep(1)
                self._acquired = True

                async def _renew_loop() -> None:
                    while True:
                        await asyncio.sleep(SPLIT_RENEW_INTERVAL_S)
                        if not await renew(0, "step:split", owner=self._owner, ttl_s=SPLIT_LEASE_TTL_S):
                            return

                self._renewer = asyncio.create_task(_renew_loop())
            except BaseException:
                # Гарантированное освобождение in-memory замка при любой ошибке,
                # таймауте или asyncio.CancelledError во время ожидания lease в БД.
                if self._split_locked:
                    self._split_locked = False
                    if _SPLIT_LOCK.locked():
                        _SPLIT_LOCK.release()
                raise
        return self

    async def release(self, session: Any | None = None) -> None:
        try:
            if self._renewer is not None:
                self._renewer.cancel()
                try:
                    await self._renewer
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
                self._renewer = None
            if self._acquired and self._owner:
                from app.services.work_lease import release as wl_release

                use_session = session if session is not None else self.session
                try:
                    await wl_release(0, "step:split", owner=self._owner, session=use_session)
                except Exception:  # noqa: BLE001
                    pass
        finally:
            self._acquired = False
            # ВСЕГДА гарантированно освобождаем локальный замок в памяти
            if self._split_locked:
                self._split_locked = False
                if _SPLIT_LOCK.locked():
                    _SPLIT_LOCK.release()
            elif _SPLIT_LOCK.locked():
                try:
                    _SPLIT_LOCK.release()
                except RuntimeError:
                    pass

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        sess = None if exc_type is not None else self.session
        await self.release(session=sess)


def acquire_step_lock(code: str | None, session: Any | None = None) -> StepGlobalLock:
    """Сериализовать шаг между проектами. None/неизвестный код — без lock.

    Этап 2 (D.4): поверх in-process asyncio.Lock — межпроцессный lease
    (project_id=0, unit_key="step:split"): вторая копия процесса больше
    не гоняет split параллельно. In-process Lock остаётся первым рубежом
    (без busy-wait между задачами одного процесса).
    """
    return StepGlobalLock(code, session=session)
