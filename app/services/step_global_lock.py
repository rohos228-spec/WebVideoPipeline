"""Кросс-проектные блокировки шагов (SQLite + параллельные проекты).

Иначе WORKER_MAX_PARALLEL>1: N проектов одновременно в split/img_pr/…
пишут xlsx/DB → `sqlite3.OperationalError: database is locked` + PendingRollback.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

# Шаг, где три параллельных проекта уже ловили database is locked (split frames).
_SPLIT_LOCK = asyncio.Lock()

# Коды шагов, которые нельзя гонять одновременно между проектами.
STEP_LOCK_CODES = frozenset({"split"})


def step_code_from_status(status) -> str | None:
    """ProjectStatus.running → код шага для lock (если задан)."""
    if status is None:
        return None
    if status.name == "splitting":
        return "split"
    return None


class StepGlobalLock:
    def __init__(self, code: str | None, session: Any | None = None) -> None:
        self.code = code
        self.session = session
        self._renewer: asyncio.Task | None = None
        self._owner: str | None = None
        self._acquired = False

    async def __aenter__(self) -> StepGlobalLock:
        if self.code == "split":
            await _SPLIT_LOCK.acquire()
            from app.services.work_lease import (
                acquire,
                current_owner,
                renew,
            )

            self._owner = current_owner()
            while not await acquire(0, "step:split", owner=self._owner, ttl_s=3600):
                await asyncio.sleep(1)
            self._acquired = True

            async def _renew_loop() -> None:
                # Ревью [2/4]: split дольше часа терял lease — второй
                # процесс перехватывал и гонял split параллельно.
                while True:
                    await asyncio.sleep(600)
                    if not await renew(0, "step:split", owner=self._owner, ttl_s=3600):
                        return

            self._renewer = asyncio.create_task(_renew_loop())
        return self

    async def release(self, session: Any | None = None) -> None:
        if not self._acquired:
            return
        if self._renewer is not None:
            self._renewer.cancel()
            try:
                await self._renewer
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._renewer = None
        from app.services.work_lease import release

        use_session = session if session is not None else self.session
        try:
            await release(0, "step:split", owner=self._owner, session=use_session)
        finally:
            self._acquired = False
            if _SPLIT_LOCK.locked():
                _SPLIT_LOCK.release()

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

