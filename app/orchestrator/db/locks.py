from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from typing import Any, TypeVar

from loguru import logger

from app.orchestrator.db.migrations import busy_timeout, is_locked

T = TypeVar("T")

LOOP_BUSY_MS = 200
FIRST_DELAY_S = 0.05
MAX_DELAY_S = 1.0
DEFAULT_BUDGET_S = 30.0


async def write_on_loop(
    fn: Callable[..., T],
    *args: Any,
    budget_s: float = DEFAULT_BUDGET_S,
    what: str = "",
    **kwargs: Any,
) -> T:
    what = what or getattr(fn, "__name__", "write")
    deadline = time.monotonic() + max(0.0, float(budget_s))
    delay = FIRST_DELAY_S
    tries = 0
    while True:
        tries += 1
        try:
            with busy_timeout(LOOP_BUSY_MS):
                result = fn(*args, **kwargs)
        except Exception as exc:
            if not is_locked(exc) or time.monotonic() + delay > deadline:
                if tries > 1:
                    logger.error("orch db {}: still locked after {} tries", what, tries)
                raise
            if tries == 1:
                logger.warning("orch db {}: database is locked, retrying without blocking the loop", what)
            await asyncio.sleep(delay)
            delay = min(delay * 2, MAX_DELAY_S)
            continue
        if tries > 1:
            logger.info("orch db {}: written after {} tries", what, tries)
        return result
