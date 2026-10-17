"""Social login: Telegram, Google, VK (owner: agent D).

Rules (п.36): a NEW account is created only through email registration. Socials can be linked in the
profile (any/all of them) and afterwards used to sign in. Everything is behind OAUTH_ENABLED=1 + keys;
without them start/callback answer 503 «не настроено». verify_* are the single network/crypto touchpoints.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import time
import urllib.parse
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request

from app import settings_auth
from app.services import roles
from app.services import user_accounts as ua

router = APIRouter(prefix="/auth/oauth", tags=["auth"])
PROVIDERS = ("telegram", "google", "vk")
TITLES = {"telegram": "Telegram", "google": "Google", "vk": "VK"}
_STATES: dict[str, float] = {}


def _configured(provider: str) -> bool:
    cfg = settings_auth.oauth_config(provider)
    return settings_auth.oauth_enabled() and all(cfg.values())


def providers_status() -> dict[str, bool]:
    return {p: _configured(p) for p in PROVIDERS}


def _need(provider: str) -> dict[str, str]:
    if provider not in PROVIDERS:
        raise HTTPException(404, "Неизвестный способ входа")
    if not _configured(provider):
        raise HTTPException(503, f"Вход через {TITLES[provider]} не настроен")
    return settings_auth.oauth_config(provider)


# ------------------------------------------------------------------ verifiers
def verify_telegram_payload(payload: dict[str, Any]) -> dict:
    """Telegram Login Widget: HMAC-SHA256(data_check_string, sha256(bot_token))."""
    cfg = _need("telegram")
    data = {k: str(v) for k, v in payload.items() if k != "hash" and v is not None}
    check = "\n".join(f"{k}={data[k]}" for k in sorted(data))
    secret = hashlib.sha256(cfg["bot_token"].encode()).digest()
    calc = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calc, str(payload.get("hash", ""))):
        raise HTTPException(401, "Подпись Telegram не прошла проверку")
    if time.time() - int(data.get("auth_date", "0")) > 86400:
        raise HTTPException(401, "Данные входа устарели")
    name = " ".join(x for x in (data.get("first_name"), data.get("last_name")) if x) or data.get("username", "")
    return {"external_id": data["id"], "name": name}


def _google_redirect(request: Request) -> str:
    return f"{_base(request)}/api/auth/oauth/google/callback"


def _vk_redirect(request: Request) -> str:
    return f"{_base(request)}/api/auth/oauth/vk/callback"


def _base(request: Request) -> str:
    return settings_auth.public_url() or str(request.base_url).rstrip("/")


def verify_google_token(payload: dict[str, Any], request: Request | None = None) -> dict:
    """Accepts {id_token} or {code} (authorization-code flow)."""
    cfg = _need("google")
    id_token = payload.get("id_token")
    with httpx.Client(timeout=10) as c:
        if not id_token and payload.get("code") and request is not None:
            r = c.post("https://oauth2.googleapis.com/token", data={
                "code": payload["code"], "client_id": cfg["client_id"], "client_secret": cfg["client_secret"],
                "redirect_uri": _google_redirect(request), "grant_type": "authorization_code"})
            id_token = r.json().get("id_token") if r.status_code == 200 else None
        if not id_token:
            raise HTTPException(401, "Google не подтвердил вход")
        r = c.get("https://oauth2.googleapis.com/tokeninfo", params={"id_token": id_token})
    info = r.json() if r.status_code == 200 else {}
    if info.get("aud") != cfg["client_id"] or not info.get("sub"):
        raise HTTPException(401, "Google не подтвердил вход")
    return {"external_id": info["sub"], "name": info.get("name") or info.get("email", ""), "email": info.get("email")}


def verify_vk_token(payload: dict[str, Any], request: Request | None = None) -> dict:
    """Accepts {access_token, user_id} or {code}."""
    cfg = _need("vk")
    token, uid = payload.get("access_token"), payload.get("user_id")
    with httpx.Client(timeout=10) as c:
        if not token and payload.get("code") and request is not None:
            r = c.get("https://oauth.vk.com/access_token", params={
                "client_id": cfg["client_id"], "client_secret": cfg["client_secret"],
                "redirect_uri": _vk_redirect(request), "code": payload["code"]})
            j = r.json() if r.status_code == 200 else {}
            token, uid = j.get("access_token"), j.get("user_id")
        if not token:
            raise HTTPException(401, "VK не подтвердил вход")
        r = c.get("https://api.vk.com/method/users.get", params={"access_token": token, "v": "5.199"})
    resp = (r.json().get("response") or [{}])[0] if r.status_code == 200 else {}
    if not resp.get("id") or (uid and str(resp["id"]) != str(uid)):
        raise HTTPException(401, "VK не подтвердил вход")
    return {"external_id": str(resp["id"]), "name": f"{resp.get('first_name', '')} {resp.get('last_name', '')}".strip()}


def _verify(provider: str, payload: dict, request: Request) -> dict:
    # looked up via globals() so tests can monkeypatch module attributes
    if provider == "telegram":
        return globals()["verify_telegram_payload"](payload)
    if provider == "google":
        return globals()["verify_google_token"](payload, request=request)
    if provider == "vk":
        return globals()["verify_vk_token"](payload, request=request)
    raise HTTPException(404, "Неизвестный способ входа")


# ------------------------------------------------------------------ routes
@router.get("/providers")
async def providers() -> dict:
    return {"providers": [{"id": p, "title": TITLES[p], "enabled": _configured(p)} for p in PROVIDERS]}


@router.get("/{provider}/start")
async def start(provider: str, request: Request) -> dict:
    cfg = _need(provider)
    state = secrets.token_urlsafe(16)
    _STATES[state] = time.time()
    if provider == "telegram":
        return {"provider": provider, "widget": {"bot_username": cfg["bot_username"]}, "state": state}
    if provider == "google":
        q = {"client_id": cfg["client_id"], "redirect_uri": _google_redirect(request), "response_type": "code",
             "scope": "openid email profile", "state": state, "prompt": "select_account"}
        return {"provider": provider, "url": "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(q)}
    q = {"client_id": cfg["client_id"], "redirect_uri": _vk_redirect(request), "response_type": "code",
         "v": "5.199", "state": state}
    return {"provider": provider, "url": "https://oauth.vk.com/authorize?" + urllib.parse.urlencode(q)}


@router.post("/{provider}/link")
async def link(provider: str, payload: dict, request: Request) -> dict:
    u = roles.require_user(request)
    if provider not in PROVIDERS:
        raise HTTPException(404, "Неизвестный способ входа")
    info = _verify(provider, payload, request)
    try:
        ua.link_oauth(u["id"], provider, str(info["external_id"]), info.get("name") or "")
    except ValueError as e:
        raise HTTPException(409, f"Этот аккаунт {TITLES[provider]} уже привязан к другому профилю") from e
    return {"ok": True, "oauth": ua.links_for(u["id"])}


@router.delete("/{provider}")
async def unlink(provider: str, request: Request) -> dict:
    u = roles.require_user(request)
    ua.unlink_oauth(u["id"], provider)
    return {"ok": True, "oauth": ua.links_for(u["id"])}


async def _callback(provider: str, payload: dict, request: Request) -> dict:
    if provider not in PROVIDERS:
        raise HTTPException(404, "Неизвестный способ входа")
    state = payload.get("state")
    if provider != "telegram" and state is not None and _STATES.pop(str(state), None) is None:
        raise HTTPException(400, "Сессия входа устарела, попробуйте ещё раз")
    info = _verify(provider, payload, request)
    ext = str(info["external_id"])
    current = roles.current_user(request)
    if current:  # logged in -> this is a link
        try:
            ua.link_oauth(current["id"], provider, ext, info.get("name") or "")
        except ValueError as e:
            raise HTTPException(409, f"Этот аккаунт {TITLES[provider]} уже привязан к другому профилю") from e
        return {"ok": True, "linked": True, "oauth": ua.links_for(current["id"])}
    u = ua.user_by_oauth(provider, ext)
    if not u:
        raise HTTPException(403, "Аккаунт не найден. Зарегистрируйтесь по почте и привяжите "
                                 f"{TITLES[provider]} в профиле.")
    token = ua.new_session(u["id"], request.headers.get("user-agent", ""))
    return {"ok": True, "token": token, "access_token": token, "user": ua.public(u)}


@router.post("/{provider}/callback")
async def callback_post(provider: str, payload: dict, request: Request) -> dict:
    return await _callback(provider, payload, request)


@router.get("/{provider}/callback")
async def callback_get(provider: str, request: Request) -> dict:
    return await _callback(provider, dict(request.query_params), request)
