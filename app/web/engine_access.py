"""Доступ к ручкам движка (чат оркестратора, отчёты проектов).

Наша авторизация (``app.web.auth_sessions``, ``OrchestratorAuthMiddleware``,
``ORCH_API_TOKEN``) не перенесена: личность и арендатора ставит
``app.web.identity.IdentityMiddleware`` новой линии. Здесь только решение по
роли. Пока заказчик не решил иначе, движок доступен только админу студии
(роли «оператор» в новой линии нет). В режиме владельца (учётки выключены)
ручки открыты так же, как весь остальной API, и защищены проверкой WEB_HOST в
lifespan.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import suppress
from typing import Annotated

from fastapi import Depends, HTTPException, Request


async def require_engine_operator(request: Request) -> None:
    """Зависимость роутера: в режиме учётных записей пускает только админа."""
    from app.services.studio_auth import current_identity
    from app.settings import settings

    if not settings.accounts_enabled:
        return
    ident = request.scope.get("studio_identity") or current_identity()
    if ident is None:
        raise HTTPException(status_code=401, detail="нужен вход")
    if not ident.is_admin:
        # 404, как у IdentityMiddleware для инструментов владельца.
        raise HTTPException(status_code=404, detail="ручка недоступна")


def engine_operator_id(request: Request) -> str | None:
    """Строка личности оператора: ``studio_user:<uuid>``; ``None`` в режиме владельца."""
    from app.services.studio_auth import current_identity

    ident = request.scope.get("studio_identity") or current_identity()
    return None if ident is None else "studio_user:" + str(ident.user_id)


async def engine_operator_token(request: Request) -> AsyncIterator[str | None]:
    """Замена ``app.web.auth_sessions.AuthDep`` для роутеров action_layer/change_control.

    Доступ решает ``require_engine_operator`` (личность новой линии). Прошедший
    проверку человек считается подтверждённым для ``approval._check_operator``:
    флаг ``access_guard.operator_token_verified`` ставится на время запроса
    вместо заголовка ``X-VP-Operator``, который фронт новой линии не шлёт.
    """
    await require_engine_operator(request)
    from app.services import access_guard

    marker = access_guard.operator_token_verified.set(True)
    try:
        yield engine_operator_id(request)
    finally:
        with suppress(ValueError):
            access_guard.operator_token_verified.reset(marker)


EngineAuthDep = Annotated[str | None, Depends(engine_operator_token)]
