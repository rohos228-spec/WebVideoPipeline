"""Auth/accounts settings (owner: agent D). Read from env at call time so tests and restarts pick up changes.

Env:
  VIA_AUTH_ENABLED / AUTH_REQUIRED   1 -> API requires a session (default OFF locally)
  VIA_ACCOUNTS_DB                    path to SQLite accounts DB (default <DATA_DIR>/accounts.db)
  VIA_OWNER_EMAIL                    email that becomes role=owner on registration
  VIA_PUBLIC_URL                     base URL for links in emails (default: request origin)
  MAIL_BACKEND                       console (default) | smtp | memory
  MAIL_FROM, SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_SSL, SMTP_STARTTLS
  OAUTH_ENABLED                      1 -> social login endpoints active (default 0)
  TELEGRAM_LOGIN_BOT_TOKEN, TELEGRAM_LOGIN_BOT_USERNAME
  GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET
  VK_APP_ID, VK_APP_SECRET
"""
from __future__ import annotations

import os
from pathlib import Path

_TRUE = {"1", "true", "yes", "on"}


def _flag(*names: str) -> bool:
    return any(os.environ.get(n, "").strip().lower() in _TRUE for n in names)


def auth_enabled() -> bool:
    if _flag("VIA_AUTH_ENABLED", "AUTH_REQUIRED"):
        return True
    try:  # legacy main: single .env user
        from app.settings import settings
        return bool(settings.web_auth_enabled)
    except Exception:  # noqa: BLE001
        return False


def accounts_db_path() -> Path:
    p = os.environ.get("VIA_ACCOUNTS_DB")
    if p:
        return Path(p)
    d = os.environ.get("VIA_DATA_DIR")
    if not d:
        try:
            from app.settings import settings
            d = str(settings.data_dir)
        except Exception:  # noqa: BLE001
            d = "./data"
    return Path(d) / "accounts.db"


def owner_email() -> str:
    return os.environ.get("VIA_OWNER_EMAIL", "").strip().lower()


def public_url() -> str:
    return os.environ.get("VIA_PUBLIC_URL", "").rstrip("/")


def mail_backend() -> str:
    b = os.environ.get("MAIL_BACKEND", "").strip().lower()
    if b:
        return b
    return "console"


def mail_from() -> str:
    return os.environ.get("MAIL_FROM") or os.environ.get("SMTP_USER") or "no-reply@via.local"


def oauth_enabled() -> bool:
    return _flag("OAUTH_ENABLED")


def oauth_config(provider: str) -> dict[str, str]:
    e = os.environ.get
    if provider == "telegram":
        return {"bot_token": e("TELEGRAM_LOGIN_BOT_TOKEN", ""), "bot_username": e("TELEGRAM_LOGIN_BOT_USERNAME", "")}
    if provider == "google":
        return {"client_id": e("GOOGLE_CLIENT_ID", ""), "client_secret": e("GOOGLE_CLIENT_SECRET", "")}
    if provider == "vk":
        return {"client_id": e("VK_APP_ID", ""), "client_secret": e("VK_APP_SECRET", "")}
    return {}


def test_hooks_enabled() -> bool:
    return _flag("VIA_TEST_HOOKS")
