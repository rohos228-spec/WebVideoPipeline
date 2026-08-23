"""Вход. Точнее — его отсутствие в режиме SaaS.

Своей формы входа у студии быть не должно (`docs/SAAS-PIVOT.md` §4.1):
единственный источник личности — биллинг, а токены в памяти процесса
(`app/web/auth_sessions.py`) теряются при рестарте и не знают ни про
арендатора, ни про баланс.

Файл не удалён, а обезврежен, и вот почему. Пароль отсюда сегодня держит
живую панель флота на машине владельца — единственный интерфейс, который у
системы вообще есть, пока фронт студии не написан (этап 3). Удалить вход
сейчас значит снять защиту с работающей установки ради чистоты, которая
никому ещё не пригодится. Поэтому: как только задан `BILLING_JWT_SECRET`,
ручка `/auth/login` отвечает 410 и токенов не выдаёт — «своей формы входа
нет» становится буквальной правдой ровно в том режиме, где это требуется.
Файл и `auth_sessions.py` уходят вместе с панелью флота.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.settings import settings
from app.web.auth_sessions import issue_session_token

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginBody(BaseModel):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=200)


@router.post("/login")
async def login(body: LoginBody) -> dict:
    if settings.sso_enabled:
        raise HTTPException(
            status_code=410,
            detail="студия не заводит своих учётных записей: вход через биллинг",
        )
    if not settings.web_auth_enabled:
        return {"ok": True, "token": None, "auth_required": False}
    user = settings.web_auth_user.strip()
    pwd = settings.web_auth_password
    if body.username != user or body.password != pwd:
        raise HTTPException(status_code=401, detail="invalid credentials")
    token = issue_session_token(body.username)
    return {"ok": True, "token": token, "auth_required": True}


@router.get("/status")
async def auth_status() -> dict:
    """Какой режим входа действует. Единственная ручка до токена.

    Фронт обязан различать три состояния, и они не сводятся к «вошёл или
    нет»: вход в биллинге, вход по локальному паролю, входа нет вовсе.
    """
    return {
        "auth_required": settings.web_auth_enabled or settings.sso_enabled,
        "sso": settings.sso_enabled,
        "brand": settings.studio_brand,
    }
