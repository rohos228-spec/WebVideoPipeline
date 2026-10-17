"""Accounts API (owner: agent D).

/api/auth/*     register, verify-email, login, logout, me, sessions, forgot/reset password (+ legacy aliases)
/api/auth/oauth social login/link (see oauth.py)
/api/profile/*  me, roles, team
/api/admin/*    users (admin/owner)
Legacy main login (single WEB_AUTH_USER/WEB_AUTH_PASSWORD from .env) keeps working.
"""

from __future__ import annotations

import re

from fastapi import Response, APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app import settings_auth
from app.services import roles
from app.services import user_accounts as ua
from app.settings import settings
from app.web.auth_sessions import _extract_bearer, is_legacy_token, issue_session_token, revoke_legacy_token
from app.web.routers import oauth

router = APIRouter()
auth = APIRouter(prefix="/auth", tags=["auth"])
profile = APIRouter(prefix="/profile", tags=["profile"])
admin = APIRouter(prefix="/admin", tags=["admin"])

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_USER = re.compile(r"^[a-zA-Z0-9_.-]{3,32}$")
_ROLES_MANAGE = Depends(roles.require_perm("roles.manage"))
_ADMIN_USERS = Depends(roles.require_perm("admin.users"))
_PW_MSG = "Пароль: минимум 8 символов, буквы и цифры"


def _pw_ok(pw: str) -> bool:
    return bool(len(pw) >= 8 and re.search(r"[A-Za-zА-Яа-яЁё]", pw) and re.search(r"\d", pw))


def base_url(req: Request) -> str:
    return settings_auth.public_url() or (req.headers.get("origin") or str(req.base_url)).rstrip("/")


class LoginBody(BaseModel):
    email: str | None = Field(None, max_length=200)
    username: str | None = Field(None, max_length=200)  # username or email (legacy UI)
    password: str = Field(min_length=1, max_length=200)


class RegisterBody(BaseModel):
    email: str = Field(max_length=200)
    password: str = Field(max_length=200)
    username: str | None = Field(None, max_length=32)
    name: str = Field("", max_length=80)


class EmailBody(BaseModel):
    email: str = Field(max_length=200)


class ResetBody(BaseModel):
    token: str = Field(max_length=200)
    password: str = Field(max_length=200)


class VerifyBody(BaseModel):
    token: str | None = Field(None, max_length=200)
    code: str | None = Field(None, max_length=6)


class PasswordBody(BaseModel):
    password: str = Field(max_length=200)


class ChangePasswordBody(BaseModel):
    old_password: str = Field(max_length=200)
    new_password: str = Field(max_length=200)


class RoleBody(BaseModel):
    role: str = Field(max_length=20)


class InviteBody(BaseModel):
    email: str = Field(max_length=200)


# ------------------------------------------------------------------ auth

def _set_session_cookie(response: Response, token: str | None) -> None:
    """Same token as Bearer, also as HttpOnly cookie: covers plain fetch/EventSource/img in the studio."""
    if token:
        response.set_cookie("vp_session", token, httponly=True, samesite="lax", max_age=60 * 60 * 24 * 30, path="/")

@auth.get("/status")
async def auth_status(request: Request) -> dict:
    u = roles.current_user(request)
    return {"auth_required": settings_auth.auth_enabled(), "registration": True,
            "mail_configured": settings_auth.mail_backend() == "smtp",
            "oauth": oauth.providers_status(), "user": ua.public(u) if u else None}


@auth.post("/register")
async def register(body: RegisterBody, request: Request, response: Response) -> dict:
    email = body.email.strip().lower()
    if not _EMAIL.match(email):
        raise HTTPException(422, "Некорректный email")
    username = (body.username or "").strip() or None
    if username and not _USER.match(username):
        raise HTTPException(422, "Логин: 3–32 символа, латиница, цифры, _ . -")
    if not _pw_ok(body.password):
        raise HTTPException(422, _PW_MSG)
    try:
        u = ua.create_user(email, body.password, username=username, name=body.name.strip())
    except ValueError as e:
        raise HTTPException(409, "Email уже зарегистрирован" if str(e) == "email_taken" else "Логин занят") from e
    row = ua.get(u["id"])
    sent = ua.send_verification(row, base_url(request))
    token = ua.new_session(u["id"], request.headers.get("user-agent", ""))
    _set_session_cookie(response, token)
    return {"ok": True, "token": token, "access_token": token, "user": u, **sent}


@auth.post("/login")
async def login(body: LoginBody, request: Request, response: Response) -> dict:
    login_ = (body.email or body.username or "").strip()
    if not login_:
        raise HTTPException(422, "Укажите почту или логин")
    u = ua.get_by_login(login_)
    if u and ua.verify_password(body.password, u["password_hash"]):
        token = ua.new_session(u["id"], request.headers.get("user-agent", ""))
        _set_session_cookie(response, token)
        return {"ok": True, "token": token, "access_token": token,
                "auth_required": settings_auth.auth_enabled(), "user": ua.public(u)}
    legacy_user = settings.web_auth_user.strip()
    if legacy_user and settings.web_auth_password and login_ == legacy_user and body.password == settings.web_auth_password:
        token = issue_session_token(login_)
        _set_session_cookie(response, token)
        return {"ok": True, "token": token, "access_token": token, "auth_required": True, "user": None}
    raise HTTPException(401, "Неверный логин или пароль")


@auth.post("/logout")
async def logout(request: Request, response: Response) -> dict:
    tok = _extract_bearer(request)
    if tok:
        ua.drop_session(tok)
        revoke_legacy_token(tok)
    response.delete_cookie("vp_session", path="/")
    return {"ok": True}


@auth.get("/me")
async def me(request: Request) -> dict:
    u = roles.current_user(request)
    if u:
        return ua.public(u) | {"effective_plan": roles.effective_plan(u), "team_allowed": roles.team_allowed(u)}
    tok = _extract_bearer(request)
    if is_legacy_token(tok):
        return {"id": None, "username": settings.web_auth_user, "role": "owner", "legacy": True, "oauth": []}
    raise HTTPException(401, "Требуется вход")


@auth.post("/verify-email")
@auth.post("/verify")
async def verify_email(body: VerifyBody, request: Request) -> dict:
    if body.token:
        if not ua.verify_by_token(body.token.strip()):
            raise HTTPException(400, "Ссылка недействительна или устарела")
        return {"ok": True}
    if body.code:
        u = roles.require_user(request)
        if not ua.verify_by_code(u["id"], body.code.strip()):
            raise HTTPException(400, "Неверный или просроченный код")
        return {"ok": True}
    raise HTTPException(422, "Нужен токен или код")


@auth.post("/verify-email/resend")
@auth.post("/verify/resend")
async def verify_resend(request: Request) -> dict:
    u = roles.require_user(request)
    if u["email_verified"]:
        return {"ok": True, "already_verified": True}
    return {"ok": True, **ua.send_verification(u, base_url(request))}


@auth.post("/forgot-password")
@auth.post("/forgot")
async def forgot(body: EmailBody, request: Request) -> dict:
    u = ua.get_by_login(body.email.strip())
    if u:
        ua.send_reset(u, base_url(request))
    return {"ok": True}  # same answer whether or not the account exists


@auth.get("/reset/check")
async def reset_check(token: str) -> dict:
    return {"valid": ua.check_reset(token)}


@auth.post("/reset-password")
@auth.post("/reset")
async def reset(body: ResetBody) -> dict:
    if not _pw_ok(body.password):
        raise HTTPException(422, _PW_MSG)
    if not ua.reset_password(body.token, body.password):
        raise HTTPException(400, "Ссылка недействительна или устарела")
    return {"ok": True}


@auth.post("/change-password")
async def change_password(body: ChangePasswordBody, request: Request, response: Response) -> dict:
    u = roles.require_user(request)
    if not ua.verify_password(body.old_password, u["password_hash"]):
        raise HTTPException(401, "Неверный пароль")
    if not _pw_ok(body.new_password):
        raise HTTPException(422, _PW_MSG)
    ua.set_password(u["id"], body.new_password)
    token = ua.new_session(u["id"], request.headers.get("user-agent", ""))
    _set_session_cookie(response, token)
    return {"ok": True, "token": token}


@auth.get("/sessions")
async def sessions(request: Request) -> dict:
    u = roles.require_user(request)
    return {"sessions": ua.list_sessions(u["id"], _extract_bearer(request))}


@auth.delete("/sessions/{sid}")
async def revoke_session(sid: int, request: Request) -> dict:
    u = roles.require_user(request)
    if not ua.drop_session_id(u["id"], sid):
        raise HTTPException(404, "Сессия не найдена")
    return {"ok": True}


@auth.post("/delete")
async def delete_account(body: PasswordBody, request: Request) -> dict:
    u = roles.require_user(request)
    if not ua.verify_password(body.password, u["password_hash"]):
        raise HTTPException(401, "Неверный пароль")
    if u["role"] == "owner":
        raise HTTPException(400, "Владельца студии удалить нельзя")
    ua.delete_user(u["id"])
    return {"ok": True}


# ------------------------------------------------------------------ profile
@profile.get("/me")
async def profile_me(request: Request) -> dict:
    u = roles.require_user(request)
    return ua.public(u) | {"effective_plan": roles.effective_plan(u), "team_allowed": roles.team_allowed(u),
                           "permissions": sorted(roles.PERMISSIONS.get(u["role"], set()))}


@profile.get("/roles")
async def profile_roles(actor=_ROLES_MANAGE) -> dict:
    return {"roles": list(ua.ROLES), "assignable": sorted(roles.assignable_by(actor["role"])),
            "users": [{"id": x["id"], "email": x["email"], "role": x["role"]} for x in ua.list_users()]}


@profile.patch("/roles/{user_id}")
async def set_role(user_id: int, body: RoleBody, request: Request) -> dict:
    actor = roles.require_user(request)
    if body.role not in ua.ROLES:
        raise HTTPException(400, "Неизвестная роль")
    target = ua.get(user_id)
    if not target:
        raise HTTPException(404, "Пользователь не найден")
    if not roles.can_change_role(actor, target, body.role):
        raise HTTPException(403, "Недостаточно прав")
    ua.set_role(user_id, body.role)
    return {"ok": True, "user": ua.public(ua.get(user_id))}


@profile.get("/team")
async def team(request: Request) -> dict:
    u = roles.require_user(request)
    owner_id = u["team_owner_id"] if u["role"] == "member" and u["team_owner_id"] else u["id"]
    return {"team_allowed": roles.team_allowed(u), "owner_id": owner_id, "members": ua.team_of(owner_id)}


@profile.post("/team")
async def team_add(body: InviteBody, request: Request) -> dict:
    u = roles.require_user(request)
    if u["role"] == "member" or not roles.can(u["role"], "team.manage"):
        raise HTTPException(403, "Недостаточно прав")
    if not roles.team_allowed(u):
        raise HTTPException(403, "Команда доступна на тарифах «Завод» и «Конвейер»")
    m = ua.get_by_login(body.email.strip())
    if not m:
        raise HTTPException(404, "Сначала коллега должен зарегистрироваться по почте")
    if m["id"] == u["id"] or m["role"] in ("owner", "admin"):
        raise HTTPException(400, "Этого пользователя нельзя добавить в команду")
    ua.set_team(m["id"], u["id"])
    ua.set_role(m["id"], "member")
    subject, text, html = ua.templates.team_invite(u["name"] or u["username"], base_url(request))
    ua.mailer.send_mail(m["email"], subject, text, html=html)
    return {"ok": True, "members": ua.team_of(u["id"])}


@profile.delete("/team/{member_id}")
async def team_remove(member_id: int, request: Request) -> dict:
    u = roles.require_user(request)
    m = ua.get(member_id)
    if not m or m["team_owner_id"] != u["id"]:
        raise HTTPException(404, "Участник не найден")
    ua.set_team(member_id, None)
    ua.set_role(member_id, "user")
    return {"ok": True}


# ------------------------------------------------------------------ admin
@admin.get("/users")
async def admin_users(_a=_ADMIN_USERS) -> dict:
    return {"users": ua.list_users()}


router.include_router(auth)
router.include_router(oauth.router)
router.include_router(profile)
router.include_router(admin)
