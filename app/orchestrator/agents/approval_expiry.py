from __future__ import annotations

import asyncio
from typing import Any

from loguru import logger

MIN_INTERVAL_S = 30.0
MAX_INTERVAL_S = 300.0


def check_interval_s() -> float:
    from app.settings import settings

    ttl = float(settings.action_layer_approval_ttl_s)
    return min(max(ttl / 6, MIN_INTERVAL_S), MAX_INTERVAL_S)


async def expire_once(supervisor: Any) -> list[int]:
    """Один проход: владелец, затем каждый арендатор под своим ``tenant_scope``.

    Задачи чата закрыты RLS: проход без арендатора видел бы только задачи
    владельца, и одобрения клиентов не истекали бы никогда.
    """
    from app.services.orch_tenancy import for_each_tenant

    try:
        closed = await for_each_tenant(supervisor.expire_stale_approvals, what="approval expiry")
    except Exception:
        logger.exception("orch approval expiry pass failed")
        return []
    if closed:
        logger.warning("orch approval expiry: cancelled tasks {}", closed)
    return closed


async def approval_expiry_loop(supervisor: Any, *, interval_s: float | None = None) -> None:
    while True:
        await expire_once(supervisor)
        await asyncio.sleep(interval_s if interval_s is not None else check_interval_s())


def start_approval_expiry(supervisor: Any) -> asyncio.Task:
    return asyncio.create_task(approval_expiry_loop(supervisor), name="orch-approval-expiry")
