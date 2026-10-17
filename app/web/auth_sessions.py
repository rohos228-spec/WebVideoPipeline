"""Web UI session auth. Tokens: account sessions (accounts DB) + legacy single .env user (main)."""

from __future__ import annotations

import secrets
import time
from typing import Annotated

from fastapi import Depends, HTTPException, Request

from app import settings_auth

_TOKENS: dict[str, float] = {}
_TOKEN_TTL_SEC = 86400 * 7


def issue_session_token(username: str) -> str:
    token = secrets.token_urlsafe(32)
    _TOKENS[token] = time.time()
    return token


def is_legacy_token(token: str | None) -> bool:
    if not token:
        return False
    issued = _TOKENS.get(token)
    if issued is None:
        return False
    if time.time() - issued > _TOKEN_TTL_SEC:
        _TOKENS.pop(token, None)
        return False
    return True


def revoke_legacy_token(token: str) -> None:
    _TOKENS.pop(token, None)


def token_is_valid(token: str | None) -> bool:
    if not token:
        return False
    try:
        from app.services import user_accounts
        if user_accounts.user_for_session(token):
            return True
    except Exception:  # noqa: BLE001
        pass
    return is_legacy_token(token)


def validate_session_token(token: str | None) -> bool:
    if not settings_auth.auth_enabled():
        return True
    return token_is_valid(token)


def _extract_bearer(request: Request) -> str | None:
    auth = request.headers.get("Authorization") or ""
    if auth.startswith("Bearer "):
        return auth.removeprefix("Bearer ").strip()
    return request.cookies.get("vp_session")


async def require_web_user(request: Request) -> str | None:
    if not settings_auth.auth_enabled():
        return None
    token = _extract_bearer(request)
    if not validate_session_token(token):
        raise HTTPException(status_code=401, detail="login required")
    return token


AuthDep = Annotated[str | None, Depends(require_web_user)]
