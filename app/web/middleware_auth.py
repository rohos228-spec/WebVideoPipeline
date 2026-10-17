"""API auth gate (owner: agent D). Active only when settings_auth.auth_enabled() (VIA_AUTH_ENABLED/AUTH_REQUIRED=1).

Register in create_app():  app.add_middleware(AuthGateMiddleware)
"""
from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from app import settings_auth
from app.web.auth_sessions import _extract_bearer, token_is_valid

PUBLIC_PREFIXES = (
    "/api/auth/",          # login, register, verify, reset, oauth callbacks
    "/api/health",
    "/api/billing/webhook/",
    "/api/billing/plans",
    "/api/docs",
    "/api/openapi.json",
)


def is_public(path: str) -> bool:
    if not path.startswith("/api/"):
        return True  # static frontend
    if path.startswith("/api/_test/"):
        return settings_auth.test_hooks_enabled()
    return path.startswith(PUBLIC_PREFIXES)


class AuthGateMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if (request.method != "OPTIONS" and settings_auth.auth_enabled()
                and not is_public(request.url.path) and not token_is_valid(_extract_bearer(request))):
            return JSONResponse({"detail": "Требуется вход"}, status_code=401)
        return await call_next(request)
