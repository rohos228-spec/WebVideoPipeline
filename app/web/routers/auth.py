"""Вход в студию: своя форма, свои учётки, свой токен.

До 2026-08-24 этот файл был заглушкой: своей формы входа у студии быть не
должно, личность приходит подписанным токеном из `llm-gateway/billing`, и
`/auth/login` отвечал 410, как только задан общий секрет. Решение владельца
сняло эту связь — платежей студия не принимает, продукт внутренний, — и вход
вернулся сюда вместе с таблицей `studio_users`.

**Токен уезжает и телом, и cookie.** Тело — для фронта: он кладёт токен в
память и ставит `Authorization: Bearer` на каждый запрос. Cookie — для
`EventSource` и WebSocket, которым заголовок поставить нечем, а без них не
работает ни один стрим прогона. Один и тот же токен в двух местах, а не два
разных: второй пришлось бы отзывать отдельно, и однажды не отозвали бы.

**Cookie ставится `HttpOnly` и `SameSite=Lax`.** `HttpOnly` — чтобы XSS на
странице не унёс сессию. `Lax` — чтобы чужой сайт не мог послать браузер с
этой cookie на наши ручки записи; при этом обычный переход по ссылке в студию
работает. `Secure` включается настройкой и по умолчанию выключен: студия
сегодня живёт на `127.0.0.1:8765` без TLS, и cookie с `Secure` там не
установилась бы вовсе — то есть вход выглядел бы сломанным.

**Задержка на подбор.** Пароль проверяется argon2 — это десятки миллисекунд, и
поток попыток дорог не столько для подбирающего, сколько для сервера: каждая
попытка занимает ядро и 64 МиБ. Поэтому счётчик неудач по адресу, и после
порога — отказ без единого хеширования.
"""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.settings import settings
from app.web.deps import get_session
from app.web.identity import COOKIE_NAME

router = APIRouter(prefix="/auth", tags=["auth"])

#: Сколько неудач подряд по одному адресу до временной блокировки.
MAX_ATTEMPTS = 8

#: Насколько блокируется адрес после порога, секунды.
LOCKOUT_SEC = 300

#: Неудачи по адресу: (число, время последней). В памяти процесса намеренно —
#: это защита от потока попыток, а не учёт. Перезапуск сбрасывает счётчик, и
#: это приемлемо: перезапускать сервер на каждой восьмой попытке дороже, чем
#: подобрать пароль. Настоящая граница — стойкость самого пароля.
_FAILURES: dict[str, tuple[int, float]] = {}


class LoginBody(BaseModel):
    email: str = Field(min_length=3, max_length=200)
    password: str = Field(min_length=1, max_length=1024)


class PasswordBody(BaseModel):
    current_password: str = Field(min_length=1, max_length=1024)
    new_password: str = Field(min_length=1, max_length=1024)


def _locked(email: str) -> int:
    """Сколько секунд ещё блокирован адрес. Ноль — не блокирован."""
    count, last = _FAILURES.get(email, (0, 0.0))
    if count < MAX_ATTEMPTS:
        return 0
    left = int(LOCKOUT_SEC - (time.time() - last))
    if left <= 0:
        _FAILURES.pop(email, None)
        return 0
    return left


def _note_failure(email: str) -> None:
    count, _ = _FAILURES.get(email, (0, 0.0))
    _FAILURES[email] = (count + 1, time.time())


def _note_success(email: str) -> None:
    _FAILURES.pop(email, None)


def _set_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=max(1, int(settings.session_ttl_hours)) * 3600,
        httponly=True,
        samesite="lax",
        secure=bool(settings.session_cookie_secure),
        path="/",
    )


@router.post("/login")
async def login(
    body: LoginBody,
    response: Response,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Проверить пару адрес/пароль и выдать сессионный токен."""
    from app.services import studio_users
    from app.services.studio_auth import issue_token

    if not settings.accounts_enabled:
        raise HTTPException(
            status_code=410,
            detail=(
                "учётных записей в этой установке нет: STUDIO_SESSION_SECRET не задан, "
                "студия работает в режиме владельца"
            ),
        )

    email = studio_users.normalize_email(body.email)
    left = _locked(email)
    if left:
        raise HTTPException(
            status_code=429,
            detail=f"слишком много неудачных попыток, подождите {left} с",
            headers={"Retry-After": str(left)},
        )

    try:
        result = await studio_users.authenticate(session, email=email, password=body.password)
    except studio_users.UserError as exc:
        _note_failure(email)
        # 401 и один общий текст: различать «нет адреса» и «не тот пароль»
        # значит превратить форму входа в справочник заведённых аккаунтов.
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    _note_success(email)
    await session.commit()

    token = issue_token(result.identity)
    _set_cookie(response, token)
    return {
        "ok": True,
        "token": token,
        "email": result.identity.email,
        "role": result.identity.role,
        "expires_in": max(1, int(settings.session_ttl_hours)) * 3600,
    }


@router.post("/logout")
async def logout(response: Response) -> dict:
    """Забыть cookie на этом устройстве.

    Токен при этом остаётся действительным до истечения срока: погасить
    подписанный токен нечем, и делать вид, что выход что-то отзывает, — хуже,
    чем сказать правду. Отзыв всех сессий разом — смена пароля
    (`POST /auth/password`), она поднимает `token_epoch`.
    """
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True, "revoked_all": False}


@router.post("/password")
async def change_password(
    body: PasswordBody,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Сменить свой пароль. Все прочие сессии при этом гаснут."""
    from app.services import studio_users
    from app.services.studio_auth import identity_of, issue_token

    identity = request.scope.get("studio_identity")
    if identity is None:
        raise HTTPException(status_code=401, detail="нужен вход")

    user = await studio_users.find_by_email(session, identity.email)
    if user is None:
        raise HTTPException(status_code=401, detail="учётной записи больше нет")

    try:
        await studio_users.authenticate(session, email=user.email, password=body.current_password)
    except studio_users.UserError as exc:
        raise HTTPException(status_code=401, detail="текущий пароль не подошёл") from exc

    try:
        await studio_users.set_password(session, user, body.new_password)
    except studio_users.UserError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await session.commit()

    # Свою сессию не рвём: человек только что доказал, что это он. Новый токен
    # несёт новое поколение, старый — вместе со всеми остальными — мёртв.
    token = issue_token(identity_of(user))
    _set_cookie(response, token)
    return {"ok": True, "token": token, "revoked_others": True}


@router.get("/status")
async def auth_status() -> dict:
    """Какой режим входа действует. Единственная ручка до токена.

    Фронт обязан различать два состояния, и они не сводятся к «вошёл или нет»:
    вход по учётной записи и работа без личностей вовсе (режим владельца на
    одной машине). В первом случае надо показать форму, во втором — сразу всё.
    """
    return {
        "auth_required": settings.accounts_enabled,
        "accounts": settings.accounts_enabled,
    }
