"""Roles and per-request user context (owner: agent D). Billing/limits (agent E) read plan/role from here.

Roles: owner > admin > user; member = team seat inside a Завод/Конвейер account (inherits the team owner's plan).
"""
from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request

from app.services import user_accounts as ua

RANK = {"owner": 3, "admin": 2, "user": 1, "member": 1}
# Plans that may have team members (п.35: Завод и выше).
TEAM_PLANS = {"zavod", "konveyer"}

PERMISSIONS: dict[str, set[str]] = {
    "owner": {"admin.users", "roles.manage", "billing.manage", "team.manage", "projects.all"},
    "admin": {"admin.users", "roles.manage", "billing.manage", "team.manage"},
    "user": {"billing.manage", "team.manage"},
    "member": set(),
}


def can(role: str, perm: str) -> bool:
    return perm in PERMISSIONS.get(role, set())


def assignable_by(actor_role: str) -> set[str]:
    """Roles an actor may hand out (nobody can grant owner through the API)."""
    if actor_role == "owner":
        return {"admin", "user", "member"}
    if actor_role == "admin":
        return {"user", "member"}
    return set()


def can_change_role(actor: Any, target: Any, new_role: str) -> bool:
    if actor["id"] == target["id"]:
        return False
    if not can(actor["role"], "roles.manage"):
        return False
    if RANK.get(target["role"], 0) >= RANK[actor["role"]]:
        return False
    return new_role in assignable_by(actor["role"])


def effective_plan(u: Any) -> str | None:
    u = dict(u)
    if u.get("role") == "member" and u.get("team_owner_id"):
        owner = ua.get(u["team_owner_id"])
        if owner:
            return owner["plan"]
    return u.get("plan")


def team_allowed(u: Any) -> bool:
    return (effective_plan(u) or "") in TEAM_PLANS


def _token(request: Request) -> str | None:
    from app.web.auth_sessions import _extract_bearer
    return _extract_bearer(request)


def current_user(request: Request):
    """sqlite Row of the logged-in account or None (legacy .env login has no account)."""
    cached = getattr(request.state, "via_user", None)
    if cached is not None:
        return cached or None
    u = ua.user_for_session(_token(request))
    request.state.via_user = u or False
    return u


def user_context(request: Request) -> dict | None:
    """For agent E (stream limits): {'user_id','role','plan','team_owner_id','email_verified'} or None."""
    u = current_user(request)
    if not u:
        return None
    return {"user_id": u["id"], "role": u["role"], "plan": effective_plan(u),
            "team_owner_id": u["team_owner_id"], "email_verified": bool(u["email_verified"])}


def require_user(request: Request):
    u = current_user(request)
    if not u:
        raise HTTPException(401, "Требуется вход")
    return u


def require_verified_user(request: Request):
    """Use before checkout/payments (п.34): email must be confirmed."""
    u = require_user(request)
    if not u["email_verified"]:
        raise HTTPException(403, "Подтвердите почту, чтобы продолжить")
    return u


def require_perm(perm: str):
    def dep(request: Request):
        u = require_user(request)
        if not can(u["role"], perm):
            raise HTTPException(403, "Недостаточно прав")
        return u
    return dep
