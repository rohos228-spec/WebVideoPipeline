"""Кросс-проектные блокировки шагов (SQLite + параллельные проекты).

Иначе WORKER_MAX_PARALLEL>1: N проектов одновременно в split/img_pr/…
пишут xlsx/DB → `sqlite3.OperationalError: database is locked` + PendingRollback.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

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


@asynccontextmanager
async def acquire_step_lock(code: str | None) -> AsyncIterator[None]:
    """Сериализовать шаг между проектами. None/неизвестный код — без lock.

    Этап 2 (D.4): поверх in-process asyncio.Lock — межпроцессный lease
    (project_id=0, unit_key="step:split"): вторая копия процесса больше
    не гоняет split параллельно. In-process Lock остаётся первым рубежом
    (без busy-wait между задачами одного процесса).
    """
    if code == "split":
        async with _SPLIT_LOCK:
            from app.services.work_lease import (
                acquire,
                current_owner,
                release,
                renew,
            )

            me = current_owner()
            while not await acquire(0, "step:split", owner=me, ttl_s=3600):
                await asyncio.sleep(1)

            async def _renew_loop() -> None:
                # Ревью [2/4]: split дольше часа терял lease — второй
                # процесс перехватывал и гонял split параллельно.
                while True:
                    await asyncio.sleep(600)
                    if not await renew(0, "step:split", owner=me, ttl_s=3600):
                        return

            renewer = asyncio.create_task(_renew_loop())
            try:
                yield
            finally:
                renewer.cancel()
                try:
                    await renewer
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
                await release(0, "step:split", owner=me)
    else:
        yield
