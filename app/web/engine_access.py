"""Доступ к ручкам движка (чат оркестратора, слой действий, контроль изменений, отчёты).

Наша авторизация (``app.web.auth_sessions``, ``OrchestratorAuthMiddleware``,
``ORCH_API_TOKEN``) не перенесена: личность и арендатора ставит
``app.web.identity.IdentityMiddleware`` новой линии. Здесь только решение по
роли.

Чат оркестратора — продукт для каждого клиента студии, а не инструмент
владельца: участнику (роль ``member``) открыто всё, что работает с его
данными, — чат, планы и одобрения, журнал действий, откат, предложения
изменений его проектов. Изоляцию держит RLS (ревизия 0020) и арендатор в
имени сессии (``app/services/orch_tenancy.py``): чужой план, задача или сессия
для участника не существуют и отвечают 404.

Только админу (``require_engine_admin``, 403) — инструменты студии целиком:
реестр неснятых резервов, аварийный стоп, общие отчёты по нескольким
проектам. В режиме владельца (учётки выключены) ручки открыты так же, как
весь остальной API, и защищены проверкой WEB_HOST в lifespan.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager, suppress
from typing import Annotated

from fastapi import Depends, HTTPException, Request


def _identity(request: Request):
    from app.services.studio_auth import current_identity

    return request.scope.get("studio_identity") or current_identity()


async def require_engine_member(request: Request) -> None:
    """Зависимость роутера: в режиме учётных записей нужна любая живая личность.

    Личность сверяет с базой ``IdentityMiddleware``; здесь вторая стена на
    случай, если путь когда-нибудь окажется в ``PUBLIC_PATHS``.
    """
    from app.settings import settings

    if not settings.accounts_enabled:
        return
    if _identity(request) is None:
        raise HTTPException(status_code=401, detail="нужен вход")


async def require_engine_admin(request: Request) -> None:
    """Инструмент студии целиком: в режиме учётных записей только админ.

    403, а не 404: ручка — часть того же продукта, что открыт участнику
    (реестр резервов в панели чата), и прятать её существование незачем.
    """
    from app.settings import settings

    if not settings.accounts_enabled:
        return
    ident = _identity(request)
    if ident is None:
        raise HTTPException(status_code=401, detail="нужен вход")
    if not ident.is_admin:
        raise HTTPException(status_code=403, detail="доступно только админу студии")


#: Прежнее имя: проверка «только админ». Новые ручки берут одно из двух выше.
require_engine_operator = require_engine_admin


def engine_operator_id(request: Request) -> str | None:
    """Строка личности оператора: ``studio_user:<uuid>``; ``None`` в режиме владельца."""
    ident = _identity(request)
    return None if ident is None else "studio_user:" + str(ident.user_id)


@contextmanager
def _verified_operator() -> Iterator[None]:
    """Человек прошёл проверку роли — он подтверждённый оператор на время запроса.

    Флаг ``access_guard.operator_token_verified`` ставится вместо заголовка
    ``X-VP-Operator``, который фронт новой линии не шлёт.
    """
    from app.services import access_guard

    marker = access_guard.operator_token_verified.set(True)
    try:
        yield
    finally:
        with suppress(ValueError):
            access_guard.operator_token_verified.reset(marker)


async def engine_operator_token(request: Request) -> AsyncIterator[str | None]:
    """Замена ``app.web.auth_sessions.AuthDep`` для роутеров action_layer/change_control.

    Участник и админ — оба операторы своих данных: решение по роли —
    ``require_engine_member``, изоляция — RLS.
    """
    await require_engine_member(request)
    with _verified_operator():
        yield engine_operator_id(request)


async def engine_admin_token(request: Request) -> AsyncIterator[str | None]:
    """То же для инструментов студии: только админ (403 участнику)."""
    await require_engine_admin(request)
    with _verified_operator():
        yield engine_operator_id(request)


EngineAuthDep = Annotated[str | None, Depends(engine_operator_token)]
EngineAdminDep = Annotated[str | None, Depends(engine_admin_token)]
