"""Кто пришёл по HTTP — и как это доезжает до SQL.

Между «в заголовке лежит токен биллинга» и «SELECT видит только своё» стоят
две вещи: разбор токена (`app/services/billing_jwt.py`) и вот этот слой,
который кладёт арендатора в ContextVar на время запроса. Дальше его подхватит
`after_begin` в `app/db.py`, и ни один вызывающий не обязан об этом помнить.

**Почему middleware, а не зависимость FastAPI.** Зависимость — это требование
к автору роутера: не забыть добавить. В кодовой базе 324 файла, тридцать с
лишним роутеров, и весь разбор дефектов в `docs/SHOTS-FIX-2026-08-23.md` §11
о том, чем кончаются требования, которые некому проверить. Middleware стоит
на входе: новый роутер закрыт по факту существования, а не по внимательности.

**Почему сырой ASGI, а не `BaseHTTPMiddleware`.** Во-первых, ContextVar:
`BaseHTTPMiddleware` исполняет приложение в отдельной задаче, и связь
контекста с обработчиком перестаёт быть очевидной. Во-вторых, стримы: у нас
SSE (`sse-starlette`) и WebSocket на каждом прогоне шага, а
`BaseHTTPMiddleware` известен тем, что ломает потоковые ответы, буферизуя их.
Сырой ASGI не делает ни того, ни другого — он вызывает следующий слой в той
же задаче.

**Где токен.** Заголовок `Authorization: Bearer` — основной путь. Cookie
`vp_session` — для `EventSource` и WebSocket, которые заголовков ставить не
умеют. Запросный параметр не поддерживается намеренно: токен из query-строки
оседает в логах прокси и в истории браузера, а живёт он неделю.

**Режим владельца.** Пока `BILLING_JWT_SECRET` не задан, слой не делает
ничего: арендатора нет, изоляции нет, работает старый вход одним паролем.
Это состояние сегодняшней установки на машине владельца, и ломать её до того,
как появится фронт студии, незачем.
"""

from __future__ import annotations

import json
from contextlib import suppress
from http.cookies import SimpleCookie

from app.services.billing_jwt import BillingAuthError, BillingIdentity, decode_token

#: Cookie с тем же токеном — для стримов, которым нельзя поставить заголовок.
COOKIE_NAME = "vp_session"

#: Что доступно без токена даже в режиме SaaS. Список короткий намеренно:
#: каждая строка здесь — дырка, которую кто-то потом попросит расширить.
PUBLIC_PATHS: frozenset[str] = frozenset(
    {
        "/api/health",
        "/api/auth/status",
        # Не 401, а честный 410: клиент, пришедший логиниться сюда, должен
        # узнать, что входа здесь нет, а не что он «не авторизован» —
        # второе он попробует исправить, подобрав пароль.
        "/api/auth/login",
    }
)

#: Схема API. Открыта в режиме владельца и закрыта в SaaS: клиенту студии она
#: не нужна ни разу, а описание всех тридцати роутеров — готовая карта для
#: того, кто ищет, за что подёргать.
DOC_PATHS: frozenset[str] = frozenset({"/api/docs", "/api/openapi.json", "/api/docs/oauth2-redirect"})

#: Что закрывается. Остальное — оболочка SPA и статика: без неё пользователю
#: нечем показать даже приглашение войти.
PROTECTED_PREFIXES: tuple[str, ...] = ("/api/", "/ws/")


def identity_from_scope(scope: dict) -> BillingIdentity | None:
    """Личность из ASGI-scope. `None` — токена нет вовсе."""
    token = _token_from_scope(scope)
    if not token:
        return None
    return decode_token(token)


def path_requires_identity(path: str) -> bool:
    """Нужен ли токен для этого пути."""
    if path in PUBLIC_PATHS:
        return False
    return path.startswith(PROTECTED_PREFIXES)


def _is_websocket(scope: dict) -> bool:
    return scope.get("type") == "websocket"


class IdentityMiddleware:
    """Ставит арендатора на время запроса; без токена не пускает."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        from app.services.tenant import set_tenant
        from app.settings import settings

        if not settings.sso_enabled:
            # Режим владельца: арендатора нет. Явный сброс, а не «оставим как
            # было»: задача могла унаследовать контекст чужого запроса.
            set_tenant(None)
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        required = path_requires_identity(path) or path in DOC_PATHS
        try:
            identity = identity_from_scope(scope)
        except BillingAuthError as exc:
            # На публичном пути протухший токен — не повод отказывать.
            # Фронт с недельным токеном в localStorage идёт спрашивать
            # `/api/auth/status` именно затем, чтобы узнать, что делать
            # дальше; ответить ему 401 значит запереть его в цикле, где он
            # не может ни войти, ни узнать, куда входить.
            if required:
                await _refuse(scope, receive, send, str(exc))
                return
            identity = None

        if identity is None:
            if required:
                await _refuse(scope, receive, send, "нужен токен биллинга")
                return
            set_tenant(None)
            await self.app(scope, receive, send)
            return

        set_tenant(identity.tenant_id)
        scope["billing_identity"] = identity
        await self.app(scope, receive, send)


def _token_from_scope(scope: dict) -> str:
    headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
    auth = headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    raw_cookie = headers.get("cookie", "")
    if raw_cookie:
        jar = SimpleCookie()
        # Битую cookie игнорируем: чужой заголовок не должен ронять запрос.
        try:
            jar.load(raw_cookie)
        except Exception:  # noqa: BLE001
            return ""
        morsel = jar.get(COOKIE_NAME)
        if morsel is not None:
            return morsel.value.strip()
    return ""


async def _refuse(scope, receive, send, detail: str) -> None:
    """401 для HTTP, закрытие для WebSocket. Причина — в теле, не в коде."""
    if _is_websocket(scope):
        # Протокол ASGI требует сначала принять `websocket.connect`, и лишь
        # потом отвечать `accept` или `close`. Закрыть, не забрав событие,
        # у части серверов означает не отказ, а зависшее рукопожатие.
        with suppress(Exception):
            await receive()
        # Закрытие до `accept` даёт клиенту HTTP 403 — рукопожатие не
        # состоялось. Код 1008 (policy violation) для тех клиентов, которые
        # успели соединиться.
        await send({"type": "websocket.close", "code": 1008})
        return
    body = json.dumps({"detail": detail}, ensure_ascii=False).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": 401,
            "headers": [
                (b"content-type", b"application/json; charset=utf-8"),
                (b"content-length", str(len(body)).encode("ascii")),
                # Не даём браузеру предлагать свою форму ввода пароля:
                # логин живёт в биллинге, а не здесь.
                (b"www-authenticate", b'Bearer realm="billing"'),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})
