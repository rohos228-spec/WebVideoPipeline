"""Слой личности на входе: что закрыто, что открыто, кто становится арендатором.

Заменяет `tests/test_sso_middleware.py`. Проверяется не «работает ли токен» —
это `tests/test_studio_auth.py`, — а то, что закрытие висит на входе в
приложение, а не на внимательности автора роутера, и что арендатор доезжает из
заголовка в контекст задачи.

К прежнему списку добавились три вещи, которых при чужой личности быть не
могло: админ ходит по инструментам владельца, участник — нет; отозванный токен
перестаёт работать до истечения срока; своя форма входа существует и работает.
"""

from __future__ import annotations

import time
import uuid

import jwt
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.settings import settings
from app.web.api import create_app
from app.web.deps import get_session
from tests import accounts_harness as ah

# asyncio_mode = "auto" в pyproject: асинхронные тесты подхватываются сами,
# а модульная метка вешала бы её и на синхронные — pytest на это ругается.


@pytest_asyncio.fixture
async def env(tmp_path, monkeypatch):
    """Приложение в режиме учётных записей, с админом и участником."""
    ah.configure(monkeypatch)
    engine, factory = await ah.make_engine(tmp_path / "identity.db")
    ah.bind_identity_session(monkeypatch, factory)

    async def _gen():
        async with factory() as s:
            yield s

    app = create_app()
    app.dependency_overrides[get_session] = _gen

    member = await ah.make_account(factory, email="member@studio.local")
    admin = await ah.make_admin(factory)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield {"client": c, "member": member, "admin": admin, "factory": factory}
    await engine.dispose()


@pytest_asyncio.fixture
async def owner_client(tmp_path, monkeypatch):
    """Режим владельца: секрета нет, слой личности не вмешивается."""
    monkeypatch.setattr(settings, "studio_session_secret", "")
    engine, factory = await ah.make_engine(tmp_path / "owner.db")
    ah.bind_identity_session(monkeypatch, factory)

    async def _gen():
        async with factory() as s:
            yield s

    app = create_app()
    app.dependency_overrides[get_session] = _gen
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    await engine.dispose()


# ── что закрыто ──────────────────────────────────────────────────────────────


async def test_api_without_token_is_refused(env) -> None:
    """Закрыт весь `/api/*`, а не перечисленные ручки.

    Список закрытого не ведётся намеренно: новый роутер обязан быть закрыт по
    факту существования, иначе первая же забытая строка — утечка.
    """
    for path in ("/api/projects", "/api/me", "/api/fleet/nodes", "/api/library/items"):
        res = await env["client"].get(path)
        assert res.status_code == 401, f"{path} отдал {res.status_code}"


#: Инструменты владельца, которые участник видеть не должен. `/api/fleet`
#: запускает команды на машинах парка, `/api/db` листает базу напрямую,
#: `/api/text-llm` и `/api/grsai` ходят к провайдерам мимо кассы,
#: `/api/prompts` правит промты платформы сразу для всех.
OWNER_TOOLS = (
    "/api/fleet/nodes",
    "/api/db/overview",
    "/api/text-llm",
    "/api/text-llm/catalog",
    "/api/grsai/status",
    "/api/prompts",
    "/api/prompt-files/global-active",
    "/api/library/items",
    "/api/gpt-workspace/sessions",
)


async def test_owner_tools_are_closed_to_members_and_open_to_admins(env) -> None:
    """Одна проверка на обе стороны — и она сама убеждается, что ручка есть.

    Раздельные тесты здесь ошибались молча: прежняя версия проверяла
    `/api/db/tables`, такого маршрута в приложении нет вовсе, и 404 приходил
    от отсутствия роутера, а не от политики. Проверка «участнику 404» на
    несуществующем пути зелена всегда и не значит ничего.

    Здесь оба утверждения стоят рядом: админ обязан получить НЕ 404, участник
    обязан получить 404. Опечатка в пути роняет первую половину, и тест
    перестаёт быть декорацией.
    """
    client = env["client"]
    for path in OWNER_TOOLS:
        as_admin = await client.get(path, headers=env["admin"].auth)
        assert as_admin.status_code != 404, f"{path} не существует или закрыт от админа"

        as_member = await client.get(path, headers=env["member"].auth)
        assert as_member.status_code == 404, f"{path} отдал {as_member.status_code} участнику"


async def test_product_surface_stays_open_to_members(env) -> None:
    """Список разрешённого легко ужать до безопасного и бесполезного.

    Этот тест держит его с другой стороны: то, ради чего человек пришёл,
    закрывать нельзя.
    """
    for path in ("/api/me", "/api/billing/balance", "/api/projects"):
        res = await env["client"].get(path, headers=env["member"].auth)
        assert res.status_code != 404, f"{path} закрыт для участника"


async def test_cost_of_goods_is_not_a_member_surface(env) -> None:
    """Себестоимость — не данные пользователя, а наши.

    `/api/llm-costs` отдаёт `cost_usd` по нодам и моделям. Данные при этом
    честно свои, деньги тоже, и путь выглядел безопасным — он и был в списке
    разрешённого. Проверяя новый путь, спрашивай и это: не видно ли отсюда,
    сколько система тратит на стороне.
    """
    for path in ("/api/projects/1/llm-costs", "/api/llm-costs/projects"):
        res = await env["client"].get(path, headers=env["member"].auth)
        assert res.status_code == 404, f"{path} отдал себестоимость участнику"


async def test_api_schema_is_closed_without_a_token(env) -> None:
    """Схема API — карта для того, кто ищет, за что подёргать."""
    assert (await env["client"].get("/api/openapi.json")).status_code == 401
    with_token = await env["client"].get("/api/openapi.json", headers=env["member"].auth)
    assert with_token.status_code == 200


# ── что открыто ──────────────────────────────────────────────────────────────


async def test_health_and_auth_status_stay_open(env) -> None:
    """До входа человеку нужно узнать, куда входить, а мониторингу — жив ли."""
    assert (await env["client"].get("/api/health")).status_code == 200
    status = await env["client"].get("/api/auth/status")
    assert status.status_code == 200
    assert status.json()["accounts"] is True


async def test_login_path_is_reachable_without_a_token(env) -> None:
    """Форма входа за токеном — это запертая изнутри дверь."""
    res = await env["client"].post(
        "/api/auth/login", json={"email": "member@studio.local", "password": ah.PASSWORD}
    )
    assert res.status_code == 200


async def test_cors_preflight_is_not_refused(env) -> None:
    """`OPTIONS` не несёт токена — и не должен получать 401.

    Слой личности стоит ВНУТРИ CORS. Наоборот было бы так: preflight получает
    401 без единого CORS-заголовка, браузер объявляет запрос заблокированным,
    и кросс-доменный фронт не работает целиком — при совершенно исправном
    токене, который он не успел отправить.
    """
    res = await env["client"].options(
        "/api/projects",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization",
        },
    )
    assert res.status_code == 200
    assert res.headers.get("access-control-allow-origin") == "*"


# ── как личность доезжает ────────────────────────────────────────────────────


async def test_token_makes_the_caller_a_tenant(env) -> None:
    """Главное: `sub` из заголовка становится арендатором запроса."""
    res = await env["client"].get("/api/me", headers=env["member"].auth)
    assert res.status_code == 200
    body = res.json()
    assert body["tenant_id"] == env["member"].user_id
    assert body["email"] == "member@studio.local"
    assert body["role"] == "member"
    assert body["accounts_enabled"] is True


async def test_cookie_works_for_streams(env) -> None:
    """`EventSource` и WebSocket заголовков не ставят — им остаётся cookie."""
    env["client"].cookies.set("vp_session", env["member"].token)
    res = await env["client"].get("/api/me")
    env["client"].cookies.clear()
    assert res.status_code == 200
    assert res.json()["tenant_id"] == env["member"].user_id


async def test_garbage_token_is_refused(env) -> None:
    res = await env["client"].get("/api/me", headers={"Authorization": "Bearer not-a-token"})
    assert res.status_code == 401


async def test_broken_cookie_does_not_crash_the_request(env) -> None:
    """Чужой или битый заголовок обязан быть проигнорирован, а не уронить запрос."""
    res = await env["client"].get("/api/health", headers={"Cookie": "=;;;not-a-cookie=="})
    assert res.status_code == 200


async def test_stale_token_does_not_lock_out_the_status_probe(env) -> None:
    """С протухшим токеном фронт обязан узнать, что делать дальше.

    Иначе он заперт: `/api/auth/status` отвечает 401, потому что токен
    просрочен, а обновить токен он не может, не узнав из статуса, что вход
    вообще есть.
    """
    stale = jwt.encode(
        {
            "sub": env["member"].user_id,
            "role": "member",
            "epoch": 0,
            "iat": int(time.time()) - 7200,
            "exp": int(time.time()) - 3600,
        },
        ah.SECRET,
        algorithm="HS256",
    )
    headers = {"Authorization": f"Bearer {stale}"}
    assert (await env["client"].get("/api/auth/status", headers=headers)).status_code == 200
    assert (await env["client"].get("/api/health", headers=headers)).status_code == 200
    # А на закрытом пути тот же токен по-прежнему не работает.
    assert (await env["client"].get("/api/me", headers=headers)).status_code == 401


# ── отзыв ────────────────────────────────────────────────────────────────────


async def test_deactivated_user_loses_access_immediately(env) -> None:
    """Уволенный сотрудник не должен работать до истечения срока токена."""
    from app.services import studio_users

    assert (await env["client"].get("/api/me", headers=env["member"].auth)).status_code == 200

    async with env["factory"]() as s:
        user = await studio_users.find_by_email(s, "member@studio.local")
        await studio_users.deactivate(s, user)
        await s.commit()

    refused = await env["client"].get("/api/me", headers=env["member"].auth)
    # Не только код, но и ПРИЧИНА. Один 401 отдают три разные ветки: нет
    # токена, подпись не сошлась, доступ отозван. Тест на голый код проходит
    # при любой из них — то есть и тогда, когда проверка отзыва вообще не
    # исполняется, а токен отвергается раньше по другой причине.
    assert refused.status_code == 401
    assert "отключена" in refused.json()["detail"], refused.text


async def test_password_change_kills_older_tokens(env) -> None:
    """Пароль меняют по подозрению на утечку — старые сессии обязаны погаснуть."""
    from app.services import studio_users

    async with env["factory"]() as s:
        user = await studio_users.find_by_email(s, "member@studio.local")
        await studio_users.set_password(s, user, "Qn8v-Ld3x-Bm6t-Wr2z")
        await s.commit()

    refused = await env["client"].get("/api/me", headers=env["member"].auth)
    assert refused.status_code == 401
    assert "обесценен" in refused.json()["detail"], refused.text


async def test_deleted_user_token_is_refused(env) -> None:
    """Токен на несуществующего пользователя подписан верно и всё равно мёртв."""
    from app.services.studio_auth import StudioIdentity, issue_token

    ghost = issue_token(
        StudioIdentity(user_id=str(uuid.uuid4()), email="ghost@studio.local", role="member", epoch=0)
    )
    res = await env["client"].get("/api/me", headers={"Authorization": f"Bearer {ghost}"})
    assert res.status_code == 401
    assert "больше нет" in res.json()["detail"], res.text


async def test_role_forged_in_the_token_does_not_grant_owner_tools(env) -> None:
    """Роль решается по строке в базе, а не по слову токена.

    Секрет знать не нужно, чтобы попробовать: достаточно, чтобы кто-то однажды
    выписал токен с ролью, которой у пользователя больше нет. Понижение админа
    обязано действовать сразу.
    """
    from app.services.studio_auth import StudioIdentity, issue_token

    forged = issue_token(
        StudioIdentity(
            user_id=env["member"].user_id,
            email=env["member"].email,
            role="admin",
            epoch=0,
        )
    )
    headers = {"Authorization": f"Bearer {forged}"}
    refused = await env["client"].get("/api/me", headers=headers)
    assert refused.status_code == 401
    assert "роль изменилась" in refused.json()["detail"], refused.text


# ── режим владельца ──────────────────────────────────────────────────────────


@pytest.mark.no_harness_gate
async def test_owner_mode_keeps_everything_open(owner_client) -> None:
    """Без `STUDIO_SESSION_SECRET` слой не вмешивается: это машина владельца."""
    res = await owner_client.get("/api/me")
    assert res.status_code == 200
    body = res.json()
    assert body["accounts_enabled"] is False
    assert body["tenant_id"] is None
    assert body["unlimited"] is True


async def test_owner_mode_keeps_its_tools(owner_client) -> None:
    """Список разрешённого действует только при учётных записях.

    Иначе правка безопасности отняла бы у владельца его же панель.
    """
    assert (await owner_client.get("/api/fleet/nodes")).status_code != 404


async def test_owner_mode_says_there_is_no_login(owner_client) -> None:
    """Заводить учётку, которая никуда не войдёт, — хуже, чем сказать «нет»."""
    res = await owner_client.post(
        "/api/auth/login", json={"email": "a@studio.local", "password": ah.PASSWORD}
    )
    assert res.status_code == 410


async def test_revoked_token_does_not_break_the_public_path(env) -> None:
    """Отозванный токен на ПУБЛИЧНОМ пути — не повод отказывать.

    Фронт со старым токеном в localStorage идёт спрашивать `/api/auth/status`
    именно затем, чтобы понять, что делать дальше. Ответить ему 401 значит
    запереть его в цикле: войти он не может, а узнать, что вход существует, —
    тоже. Ровно та же логика, что для протухшей подписи, но ветка другая:
    здесь подпись верна, а доступ отозван базой.
    """
    from app.services import studio_users

    async with env["factory"]() as s:
        user = await studio_users.find_by_email(s, "member@studio.local")
        await studio_users.deactivate(s, user)
        await s.commit()

    assert (await env["client"].get("/api/auth/status", headers=env["member"].auth)).status_code == 200
    assert (await env["client"].get("/api/health", headers=env["member"].auth)).status_code == 200
    # А на закрытом — по-прежнему отказ.
    assert (await env["client"].get("/api/me", headers=env["member"].auth)).status_code == 401


async def test_defensive_branches_answer_instead_of_crashing(env) -> None:
    """Ручки, до которых без токена не доходят, всё равно обязаны отвечать.

    `/api/me` и смена пароля читают личность из `scope`. Middleware закрывает
    `/api/*` и до этих веток не пускает — но «не пускает» держится на списке
    путей, а список правят. Если путь однажды окажется публичным по недосмотру,
    ветка обязана дать пустой ответ и 401, а не 500: пятисотка на форме входа
    выглядит как поломка сервера и уводит разбор не туда.

    Зовём функции напрямую, минуя middleware, — иначе проверить нечего.
    """
    from fastapi import HTTPException
    from starlette.requests import Request

    from app.web.routers.auth import PasswordBody, change_password
    from app.web.routers.me import me

    def _bare_request() -> Request:
        return Request({"type": "http", "method": "GET", "path": "/api/me", "headers": []})

    async with env["factory"]() as session:
        answer = await me(_bare_request(), session=session)
        assert answer.accounts_enabled is True
        assert answer.tenant_id is None
        assert answer.balance_micro == 0

    async with env["factory"]() as session:
        with pytest.raises(HTTPException) as exc:
            await change_password(
                PasswordBody(current_password="a" * 12, new_password="b" * 12),
                _bare_request(),
                response=None,
                session=session,
            )
        assert exc.value.status_code == 401
        assert "нужен вход" in exc.value.detail
