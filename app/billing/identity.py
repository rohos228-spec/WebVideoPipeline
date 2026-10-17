"""Текущий пользователь для биллинга.

Модуль аккаунтов (агент D) регистрирует резолвер через `set_resolver(fn)`,
где fn(request) -> dict{id, email, email_verified, role} | None.
Пока резолвера нет: при выключенной авторизации — локальный пользователь "local";
в тестах (VIA_TEST_HOOKS=1) — заголовок X-VIA-User-Id.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, HTTPException, Request

from app import settings_billing

_resolver: Callable[[Request], dict | None] | None = None


def set_resolver(fn: Callable[[Request], dict | None] | None) -> None:
    global _resolver
    _resolver = fn


def _auth_enabled() -> bool:
    return (os.getenv("VIA_AUTH_ENABLED") or os.getenv("AUTH_REQUIRED") or "0") in ("1", "true", "yes")


async def current_user(request: Request) -> dict:
    if _resolver is not None:
        res = _resolver(request)
        if hasattr(res, "__await__"):
            res = await res
        if res:
            return {"email_verified": False, "role": "user", **res, "id": str(res.get("id") or res.get("user_id"))}
        raise HTTPException(401, "Требуется вход")
    if settings_billing.load().test_hooks:
        uid = request.headers.get("X-VIA-User-Id")
        if uid:
            return {"id": uid, "email": f"{uid}@test.local",
                    "email_verified": request.headers.get("X-VIA-Verified", "1") == "1",
                    "role": request.headers.get("X-VIA-Role", "user")}
    if not _auth_enabled():
        return {"id": "local", "email": None, "email_verified": True, "role": "owner"}
    raise HTTPException(401, "Требуется вход")


CurrentUser = Annotated[dict, Depends(current_user)]
