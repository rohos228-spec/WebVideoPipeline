"""`/api/admin/access` — кто пришёл в админ-панель.

Ручка нужна фронту: `admin-shell` до всего остального спрашивает права и без
ответа показывает «Раздел не найден». Контракт — `AdminAccess` из
`web/src/lib/admin-api.ts`, поле в поле: `is_admin`, `auth_required`,
`bootstrap`, `user`, `reason`.

Режимы:

* **Режим владельца** (`STUDIO_SESSION_SECRET` пуст, личностей нет) — панель
  открыта, `user=None`: это его машина, спрашивать некого.
* **Аккаунты включены** — отвечаем по роли из токена. Участнику отдаём
  `is_admin=false` и человеческую причину: страница покажет «нужен вход
  администратора», а не ошибку. 401 здесь не бросаем — 401 ломает сам гейт
  (`adminFetch` на `/access` не делает автопереход, но `isError` рисует
  «Повторить» вместо объяснения).
* `bootstrap` — студия ещё не инициализирована (аккаунтов нет вовсе), первый
  зарегистрированный станет админом.

`user.id` в UI объявлен числом, но в этой ветке идентификатор учётки — UUID,
поэтому отдаём строку: фронт показывает имя и почту, по id никуда не ходит.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import StudioUser
from app.services.studio_auth import current_identity
from app.settings import settings
from app.web.deps import get_session

router = APIRouter(prefix="/admin", tags=["admin"])


class AdminUserBrief(BaseModel):
    """Минимальный портрет вошедшего — ровно то, что рисует шапка панели."""

    id: str
    email: str
    username: str
    name: str
    role: str
    plan: str | None = None
    status: str = "active"
    email_verified: bool = True
    created_at: int = 0
    last_login: int | None = None


class AdminAccess(BaseModel):
    is_admin: bool
    auth_required: bool
    bootstrap: bool
    user: AdminUserBrief | None = None
    reason: str | None = None


@router.get("/access")
async def access(session: AsyncSession = Depends(get_session)) -> AdminAccess:
    """Права текущего запроса. 200 всегда — разница в полях, а не в коде ответа."""
    auth_required = settings.accounts_enabled
    identity = current_identity()

    if identity is None:
        # Режим владельца: личностей нет, изоляции нет, панель открыта.
        return AdminAccess(
            is_admin=True,
            auth_required=auth_required,
            bootstrap=False,
            user=None,
        )

    row = await session.get(StudioUser, identity.user_id)
    if row is None:
        return AdminAccess(
            is_admin=False,
            auth_required=auth_required,
            bootstrap=False,
            reason="Учётная запись не найдена — войди заново.",
        )

    total = int((await session.execute(select(func.count()).select_from(StudioUser))).scalar() or 0)
    user = AdminUserBrief(
        id=str(row.id),
        email=row.email,
        username=row.email.split("@")[0],
        name=row.display_name or row.email.split("@")[0],
        role=row.role,
        status="active" if row.is_active else "blocked",
        email_verified=row.email_verified,
        created_at=int(row.created_at.timestamp()) if row.created_at else 0,
        last_login=int(row.last_login_at.timestamp()) if row.last_login_at else None,
    )
    if not identity.is_admin:
        return AdminAccess(
            is_admin=False,
            auth_required=auth_required,
            bootstrap=total == 0,
            user=user,
            reason="Текущий аккаунт не имеет прав администратора. Войди под админским аккаунтом.",
        )
    return AdminAccess(
        is_admin=True,
        auth_required=auth_required,
        bootstrap=total == 0,
        user=user,
    )
