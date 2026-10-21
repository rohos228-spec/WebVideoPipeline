"""Доступ к ``/api/orchestrator/**``.

Два пути. Внешний клиент — Bearer из окружения ``ORCH_API_TOKEN``,
сравнение constant-time. Студия — обычный доступ Studio: вход (если он
включён) или прямой loopback без прокси, плюс токен оператора
``X-VP-Operator``. Нет ни того, ни другого — 401. Значения токенов никуда
не пишутся.
"""

from __future__ import annotations

import hmac
import os
from http.cookies import SimpleCookie

from fastapi import HTTPException, Request

_PREFIX = "/api/orchestrator"


def is_orchestrator_path(path: str) -> bool:
    """``/api/orchestrator`` и всё под ним, включая путь без завершающего слэша."""
    if path == _PREFIX:
        return True
    return path.startswith(_PREFIX + "/")


def expected_token() -> str:
    return os.environ.get("ORCH_API_TOKEN", "")


def bearer_ok(authorization: str | None) -> bool:
    if not authorization or not authorization.startswith("Bearer "):
        return False
    presented = authorization.removeprefix("Bearer ").strip()
    expected = expected_token()
    if not presented or not expected:
        return False
    return hmac.compare_digest(presented.encode("utf-8"), expected.encode("utf-8"))


def scope_headers(scope: dict) -> dict[str, str]:
    found: dict[str, str] = {}
    for key, value in scope.get("headers") or ():
        try:
            found[key.decode("latin-1").lower()] = value.decode("utf-8")
        except (UnicodeDecodeError, AttributeError):
            continue
    return found


def studio_session_token(headers: dict[str, str]) -> str | None:
    raw = headers.get("authorization") or ""
    if raw.startswith("Bearer "):
        token = raw.removeprefix("Bearer ").strip()
        if token:
            return token
    cookie = headers.get("cookie")
    if not cookie:
        return None
    jar = SimpleCookie()
    try:
        jar.load(cookie)
    except Exception:
        return None
    morsel = jar.get("vp_session")
    return morsel.value if morsel is not None and morsel.value else None


def studio_ok(scope: dict) -> bool:
    from app.services import access_guard
    from app.settings import settings

    headers = scope_headers(scope)
    if settings.web_auth_enabled:
        from app.web.auth_sessions import validate_session_token

        if not validate_session_token(studio_session_token(headers)):
            return False
    elif not settings.orchestrator_allow_remote_without_auth:
        client = scope.get("client")
        if not access_guard.is_loopback_host(client[0] if client else None):
            return False
        if access_guard._has_forward_headers(headers):
            return False
    if access_guard.operator_token_enforced():
        return access_guard.check_operator_token(headers.get(access_guard.OPERATOR_HEADER.lower()))
    return True


def via_orch_token(scope: dict) -> bool:
    return bearer_ok(scope_headers(scope).get("authorization"))


def request_allowed(scope: dict) -> bool:
    return via_orch_token(scope) or studio_ok(scope)


async def require_orch_token(request: Request) -> None:
    """Зависимость роутера: закрывает эндпоинты, даже если middleware не повесили."""
    if not request_allowed(request.scope):
        raise HTTPException(status_code=401, detail="unauthorized")


class OrchestratorAuthMiddleware:
    """Чистый ASGI-middleware (не BaseHTTPMiddleware — тот буферизует SSE)."""

    def __init__(self, app: object) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: object, send: object) -> None:
        path = scope.get("path") or ""
        # Websocket под /api/orchestrator/** не обходит Bearer: эндпоинтов нет,
        # но открытый upgrade был бы дырой.
        if scope.get("type") == "websocket" and is_orchestrator_path(path):
            await send(  # type: ignore[misc]
                {
                    "type": "websocket.http.response.start",
                    "status": 401,
                    "headers": [(b"content-type", b"application/json")],
                }
            )
            await send(  # type: ignore[misc]
                {"type": "websocket.http.response.body", "body": b'{"detail":"unauthorized"}'}
            )
            return
        if scope.get("type") != "http":
            await self.app(scope, receive, send)  # type: ignore[misc]
            return
        if not is_orchestrator_path(path) or scope.get("method") == "OPTIONS":
            await self.app(scope, receive, send)  # type: ignore[misc]
            return
        if not request_allowed(scope):
            body = b'{"detail":"unauthorized"}'
            await send(  # type: ignore[misc]
                {
                    "type": "http.response.start",
                    "status": 401,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(body)).encode()),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body})  # type: ignore[misc]
            return
        await self.app(scope, receive, send)  # type: ignore[misc]
