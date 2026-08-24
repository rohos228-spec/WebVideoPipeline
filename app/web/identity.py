"""Кто пришёл по HTTP — и как это доезжает до SQL.

Между «в заголовке лежит токен» и «SELECT видит только своё» стоят две вещи:
разбор токена (`app/services/studio_auth.py`) и вот этот слой, который кладёт
арендатора в ContextVar на время запроса. Дальше его подхватит `after_begin` в
`app/db.py`, и ни один вызывающий не обязан об этом помнить.

**Почему middleware, а не зависимость FastAPI.** Зависимость — это требование к
автору роутера: не забыть добавить. В кодовой базе 324 файла, тридцать с лишним
роутеров, и весь разбор дефектов в `docs/SHOTS-FIX-2026-08-23.md` §11 о том,
чем кончаются требования, которые некому проверить. Middleware стоит на входе:
новый роутер закрыт по факту существования, а не по внимательности.

**Почему сырой ASGI, а не `BaseHTTPMiddleware`.** Во-первых, ContextVar:
`BaseHTTPMiddleware` исполняет приложение в отдельной задаче, и связь контекста
с обработчиком перестаёт быть очевидной. Во-вторых, стримы: у нас SSE
(`sse-starlette`) и WebSocket на каждом прогоне шага, а `BaseHTTPMiddleware`
известен тем, что ломает потоковые ответы, буферизуя их. Сырой ASGI не делает
ни того, ни другого — он вызывает следующий слой в той же задаче.

**Где токен.** Заголовок `Authorization: Bearer` — основной путь. Cookie
`vp_session` — для `EventSource` и WebSocket, которые заголовков ставить не
умеют. Запросный параметр не поддерживается намеренно: токен из query-строки
оседает в логах прокси и в истории браузера.

**Список разрешённого, а не запрещённого.** Половина API — инструменты
владельца, а не продукт: парк машин с запуском команд, прямой обозреватель
базы, ручки к провайдерам мимо кассы. Список запрещённого означал бы, что
каждый новый роутер открыт участникам, пока кто-нибудь не вспомнит его закрыть.
См. `TENANT_ALLOWED_PREFIXES`.

**Админ ходит везде.** Инструменты владельца закрыты от РОЛИ `member`, а не от
всех, у кого есть токен: админ студии — это и есть владелец, и запирать его от
парка машин и обозревателя базы значило бы оставить систему без оператора.
Решение принимается по роли ИЗ БАЗЫ (`assert_not_revoked` сверяет её с
токеном), а не по слову токена.

**Режим владельца.** Пока `STUDIO_SESSION_SECRET` не задан, слой не делает
ничего: арендатора нет, изоляции нет, вход отключён. Это состояние сегодняшней
установки на машине владельца.
"""

from __future__ import annotations

import json
from contextlib import suppress
from http.cookies import SimpleCookie

from app.services.studio_auth import AuthError, StudioIdentity, decode_token

#: Cookie с тем же токеном — для стримов, которым нельзя поставить заголовок.
COOKIE_NAME = "vp_session"

#: Что доступно без токена даже в режиме учётных записей. Список короткий
#: намеренно: каждая строка здесь — дырка, которую кто-то потом попросит
#: расширить.
PUBLIC_PATHS: frozenset[str] = frozenset(
    {
        "/api/health",
        "/api/auth/status",
        # Вход. Без него в систему нечем попасть.
        "/api/auth/login",
    }
)

#: Схема API. Открыта в режиме владельца и закрыта, когда есть учётные записи:
#: участнику студии она не нужна ни разу, а описание всех тридцати роутеров —
#: готовая карта для того, кто ищет, за что подёргать.
DOC_PATHS: frozenset[str] = frozenset({"/api/docs", "/api/openapi.json", "/api/docs/oauth2-redirect"})

#: Что закрывается. Остальное — оболочка SPA и статика: без неё пользователю
#: нечем показать даже форму входа.
PROTECTED_PREFIXES: tuple[str, ...] = ("/api/", "/ws/")

#: Что участнику (роль `member`) РАЗРЕШЕНО. Именно список разрешённого, а не
#: запрещённого, и это не педантизм.
#:
#: API писался под одного владельца за своей машиной, и половина его —
#: инструменты, а не продукт: `/api/fleet` запускает PowerShell на машинах
#: парка, `/api/db` листает базу напрямую, `/api/text-llm` и `/api/grsai`
#: ходят к провайдерам мимо кассы, `/api/prompts` правит промты платформы для
#: всех сразу. Список запрещённого означал бы, что каждый новый роутер открыт,
#: пока кто-нибудь не вспомнит его закрыть. Ровно этот способ терять изоляцию
#: разбирается в §4.2 спеки, и ровно поэтому там выбран RLS вместо фильтра в
#: коде.
#:
#: Добавлять сюда путь — значит утверждать, что он безопасен для участника:
#: отдаёт только его данные, тратит только его деньги.
#:
#: Отдельная ловушка, на которую уже наступили: `/api/llm-costs` отдаёт
#: `cost_usd` — СЕБЕСТОИМОСТЬ вызовов. Данные при этом честно свои, деньги
#: тоже, и путь выглядел безопасным. Проверяя путь, спрашивай и это: не видно
#: ли отсюда, сколько система тратит на стороне.
TENANT_ALLOWED_PREFIXES: tuple[str, ...] = (
    "/api/me",
    "/api/auth/logout",
    "/api/auth/password",
    "/api/billing/",
    "/api/chat",
    # Промты УЧАСТНИКА, а не платформы: пишут в его область, читают его
    # переопределение. `/api/prompts` и `/api/prompt-files` рядом остаются
    # закрытыми — они правят промты платформы сразу для всех.
    "/api/my-prompts",
    "/api/projects",  # проекты и всё вложенное: кадры, шаги, смета, холст
    "/api/artifacts",
    "/api/files",
    "/api/hitl",
    "/api/runs",
    "/api/runtime-streams",
    "/api/stages",
    "/api/workflows",
    "/api/node-groups",
    "/api/sidebar-layout",
    "/api/bug-reports",
)


def path_is_owner_only(path: str) -> bool:
    """Инструмент владельца, а не продукт. Для роли `member` закрыт наглухо."""
    if not path.startswith(PROTECTED_PREFIXES):
        return False
    if path in PUBLIC_PATHS or path in DOC_PATHS:
        return False
    return not path.startswith(TENANT_ALLOWED_PREFIXES)


def identity_from_scope(scope: dict) -> StudioIdentity | None:
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

        from app.services.studio_auth import set_identity
        from app.services.tenant import set_tenant
        from app.settings import settings

        if not settings.accounts_enabled:
            # Режим владельца: арендатора нет. Явный сброс, а не «оставим как
            # было»: задача могла унаследовать контекст чужого запроса.
            set_tenant(None)
            set_identity(None)
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        required = path_requires_identity(path) or path in DOC_PATHS
        try:
            identity = identity_from_scope(scope)
        except AuthError as exc:
            # На публичном пути протухший токен — не повод отказывать. Фронт
            # со старым токеном в localStorage идёт спрашивать
            # `/api/auth/status` именно затем, чтобы узнать, что делать
            # дальше; ответить ему 401 значит запереть его в цикле, где он не
            # может ни войти, ни узнать, куда входить.
            if required:
                await _refuse(scope, receive, send, str(exc))
                return
            identity = None

        if identity is None:
            if required:
                await _refuse(scope, receive, send, "нужен вход")
                return
            set_tenant(None)
            set_identity(None)
            await self.app(scope, receive, send)
            return

        # Подпись сошлась — значит токен выписывали мы. Осталось второе: жив
        # ли ещё этот доступ. Проверка идёт ДО `set_tenant`: `studio_users`
        # не принадлежит арендатору, и запрашивать её от чужого имени незачем.
        try:
            await _assert_live(identity)
        except AuthError as exc:
            if required:
                await _refuse(scope, receive, send, str(exc))
                return
            set_tenant(None)
            set_identity(None)
            await self.app(scope, receive, send)
            return

        if path_is_owner_only(path) and not identity.is_admin:
            # 404, а не 403: существование инструментов владельца — тоже
            # сведения. Участнику студии знать про парк машин незачем.
            await _refuse(
                scope,
                receive,
                send,
                "ручка недоступна",
                status=404,
            )
            return

        set_tenant(identity.tenant_id)
        set_identity(identity)
        scope["studio_identity"] = identity
        await self.app(scope, receive, send)


async def _assert_live(identity: StudioIdentity) -> None:
    """Учётка ещё существует, не отключена, токен не обесценен.

    Открывает свою сессию, а не берёт зависимость роутера: слой стоит ДО
    роутера, и зависимостей на этом уровне не существует. Стоимость — один
    поиск по первичному ключу на запрос к `/api/*`; статика и оболочка SPA
    сюда не доходят.
    """
    from app.db import SessionLocal
    from app.services.studio_auth import assert_not_revoked
    from app.services.tenant import set_tenant

    set_tenant(None)
    async with SessionLocal() as session:
        await assert_not_revoked(session, identity)


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


async def _refuse(scope, receive, send, detail: str, *, status: int = 401) -> None:
    """Отказ: 401 по умолчанию, закрытие для WebSocket."""
    if _is_websocket(scope):
        # Протокол ASGI требует сначала принять `websocket.connect`, и лишь
        # потом отвечать `accept` или `close`. Закрыть, не забрав событие, у
        # части серверов означает не отказ, а зависшее рукопожатие.
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
            "status": status,
            "headers": [
                (b"content-type", b"application/json; charset=utf-8"),
                (b"content-length", str(len(body)).encode("ascii")),
                # Не даём браузеру предлагать свою форму ввода пароля: вход у
                # студии свой, HTML-формой, а не диалогом браузера.
                (b"www-authenticate", b'Bearer realm="studio"'),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})
