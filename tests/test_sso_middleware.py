"""Слой личности на входе: что закрыто, что открыто, кто становится арендатором.

Проверяется не «работает ли JWT» — это `tests/test_billing_sso.py`, — а то,
что закрытие висит на входе в приложение, а не на внимательности автора
роутера, и что арендатор доезжает из заголовка в контекст задачи.
"""

from __future__ import annotations

import time
import uuid

import jwt
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base
from app.settings import settings
from app.web.api import create_app
from app.web.deps import get_session

SECRET = "секрет-биллинга-длиною-в-тридцать-два-байта-и-более"


def _token(sub: str | None = None, brand: str = "videostudio") -> str:
    return jwt.encode(
        {
            "sub": sub or str(uuid.uuid4()),
            "email": "client@example.com",
            "brand": brand,
            "iat": int(time.time()),
            "exp": int(time.time()) + 3600,
        },
        SECRET,
        algorithm="HS256",
    )


@pytest_asyncio.fixture
async def client(tmp_path, monkeypatch):
    """Приложение в режиме SaaS на SQLite.

    SQLite здесь законен ровно потому, что проверяется механика личности, а
    не изоляция: политик в этом движке нет, и `ALLOW_UNISOLATED_TENANTS`
    существует для таких случаев. Саму изоляцию проверяет
    `tests/test_rls_postgres.py` на живом Postgres — иначе проверять нечего.
    """
    monkeypatch.setattr(settings, "billing_jwt_secret", SECRET)
    monkeypatch.setattr(settings, "studio_brand", "")
    monkeypatch.setattr(settings, "allow_unisolated_tenants", True)
    monkeypatch.setattr(settings, "tenant_start_credits", 0.0)

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'sso.db'}", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _gen():
        async with factory() as s:
            yield s

    app = create_app()
    app.dependency_overrides[get_session] = _gen
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    await engine.dispose()


async def test_api_without_token_is_refused(client) -> None:
    """Закрыт весь `/api/*`, а не перечисленные ручки.

    Список закрытого не ведётся намеренно: новый роутер обязан быть закрыт
    по факту существования, иначе первая же забытая строка — утечка.
    """
    for path in ("/api/projects", "/api/me", "/api/fleet/nodes", "/api/library/items"):
        res = await client.get(path)
        assert res.status_code == 401, f"{path} отдал {res.status_code}"


async def test_owner_tools_are_closed_to_tenants(client) -> None:
    """Инструменты владельца не продукт, и токен арендатора их не открывает.

    `/api/fleet` запускает команды на машинах парка, `/api/db` листает базу
    напрямую, `/api/text-llm` и `/api/grsai` ходят к провайдерам мимо кассы,
    `/api/prompts` правит промты платформы сразу для всех. Клиент студии
    приходит с законным токеном — и не должен доставать ничего из этого.
    """
    headers = {"Authorization": f"Bearer {_token()}"}
    for path in (
        "/api/fleet/nodes",
        "/api/db/tables",
        "/api/text-llm/models",
        "/api/grsai/models",
        "/api/prompts",
        "/api/prompt-files",
        "/api/library/items",
        "/api/gpt-workspace/sessions",
        "/api/generation-options",
    ):
        res = await client.get(path, headers=headers)
        assert res.status_code == 404, f"{path} отдал {res.status_code} арендатору"


async def test_product_surface_stays_open_to_tenants(client) -> None:
    """Обратная половина: то, ради чего клиент пришёл, закрывать нельзя.

    Список разрешённого легко ужать до безопасного и бесполезного — этот
    тест держит его с другой стороны.
    """
    headers = {"Authorization": f"Bearer {_token()}"}
    for path in ("/api/me", "/api/billing/balance", "/api/projects"):
        res = await client.get(path, headers=headers)
        assert res.status_code != 404, f"{path} закрыт для клиента студии"


async def test_owner_mode_keeps_its_tools(tmp_path, monkeypatch) -> None:
    """На машине владельца инструменты остаются на месте.

    Список разрешённого действует только в SaaS: иначе правка безопасности
    отняла бы у владельца его же панель.
    """
    monkeypatch.setattr(settings, "billing_jwt_secret", "")
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'tools.db'}", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _gen():
        async with factory() as s:
            yield s

    app = create_app()
    app.dependency_overrides[get_session] = _gen
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        assert (await c.get("/api/fleet/nodes")).status_code != 404
    await engine.dispose()


async def test_health_and_auth_status_stay_open(client) -> None:
    """До входа клиенту нужно узнать, куда входить, а мониторингу — жив ли."""
    assert (await client.get("/api/health")).status_code == 200
    status = await client.get("/api/auth/status")
    assert status.status_code == 200
    assert status.json()["sso"] is True


async def test_api_schema_is_closed_in_saas(client) -> None:
    """Схема API — карта для того, кто ищет, за что подёргать.

    В режиме владельца она открыта и полезна; клиенту студии не нужна ни
    разу. Токен её открывает — оператору хватает.
    """
    assert (await client.get("/api/openapi.json")).status_code == 401
    with_token = {"Authorization": f"Bearer {_token()}"}
    assert (await client.get("/api/openapi.json", headers=with_token)).status_code == 200


async def test_local_login_form_is_gone_in_saas(client) -> None:
    """Своей формы входа в SaaS нет — она в биллинге."""
    res = await client.post("/api/auth/login", json={"username": "a", "password": "b"})
    assert res.status_code == 410


async def test_stale_token_does_not_lock_out_the_status_probe(client) -> None:
    """С протухшим токеном фронт обязан узнать, куда идти входить.

    Иначе он заперт: `/api/auth/status` отвечает 401, потому что токен
    просрочен, а обновить токен он не может, не узнав из статуса, что вход
    вообще в биллинге.
    """
    stale = jwt.encode(
        {
            "sub": str(uuid.uuid4()),
            "brand": "videostudio",
            "iat": int(time.time()) - 7200,
            "exp": int(time.time()) - 3600,
        },
        SECRET,
        algorithm="HS256",
    )
    headers = {"Authorization": f"Bearer {stale}"}
    assert (await client.get("/api/auth/status", headers=headers)).status_code == 200
    assert (await client.get("/api/health", headers=headers)).status_code == 200
    # А на закрытом пути тот же токен по-прежнему не работает.
    assert (await client.get("/api/me", headers=headers)).status_code == 401


async def test_cors_preflight_is_not_refused(client) -> None:
    """`OPTIONS` не несёт токена — и не должен получать 401.

    Слой личности стоит ВНУТРИ CORS. Наоборот было бы так: preflight
    получает 401 без единого CORS-заголовка, браузер объявляет запрос
    заблокированным, и кросс-доменный фронт не работает целиком — при
    совершенно исправном токене, который он не успел отправить.
    """
    res = await client.options(
        "/api/projects",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization",
        },
    )
    assert res.status_code == 200
    assert res.headers.get("access-control-allow-origin") == "*"


async def test_garbage_token_is_refused(client) -> None:
    res = await client.get("/api/me", headers={"Authorization": "Bearer not-a-token"})
    assert res.status_code == 401


async def test_token_makes_the_caller_a_tenant(client) -> None:
    """Главное: `sub` из заголовка становится арендатором запроса."""
    sub = str(uuid.uuid4())
    res = await client.get("/api/me", headers={"Authorization": f"Bearer {_token(sub)}"})
    assert res.status_code == 200
    body = res.json()
    assert body["tenant_id"] == sub
    assert body["email"] == "client@example.com"
    assert body["sso_enabled"] is True


async def test_cookie_works_for_streams(client) -> None:
    """`EventSource` и WebSocket заголовков не ставят — им остаётся cookie."""
    sub = str(uuid.uuid4())
    client.cookies.set("vp_session", _token(sub))
    res = await client.get("/api/me")
    assert res.status_code == 200
    assert res.json()["tenant_id"] == sub


async def test_first_visit_creates_the_account_once(client) -> None:
    """Регистрации нет: счёт заводится первым запросом и только раз."""
    sub = str(uuid.uuid4())
    headers = {"Authorization": f"Bearer {_token(sub)}"}
    first = (await client.get("/api/me", headers=headers)).json()
    second = (await client.get("/api/me", headers=headers)).json()
    assert first["provisioned_now"] is True
    assert second["provisioned_now"] is False
    assert second["balance_micro"] == first["balance_micro"] == 0


async def test_start_credits_are_granted_exactly_once(client, monkeypatch) -> None:
    """Подарок деньгами не повторяется на каждом запросе.

    Токен живёт неделю и приходит с каждым обращением; признак «уже
    выдавали» ищется в леджере, а не во флаге, который с ним разойдётся.
    """
    monkeypatch.setattr(settings, "tenant_start_credits", 5.0)
    sub = str(uuid.uuid4())
    headers = {"Authorization": f"Bearer {_token(sub)}"}
    first = (await client.get("/api/me", headers=headers)).json()
    second = (await client.get("/api/me", headers=headers)).json()
    assert first["balance_micro"] == 5 * 10**6
    assert second["balance_micro"] == 5 * 10**6
    assert second["balance_credits"] == "5.00"


async def test_foreign_brand_cannot_enter(client, monkeypatch) -> None:
    """Токен соседней воронки подписан верно и всё равно не наш."""
    monkeypatch.setattr(settings, "studio_brand", "videostudio")
    ok = await client.get("/api/me", headers={"Authorization": f"Bearer {_token()}"})
    assert ok.status_code == 200
    alien = _token(brand="chattiq")
    assert (await client.get("/api/me", headers={"Authorization": f"Bearer {alien}"})).status_code == 401


@pytest.mark.no_harness_gate
async def test_owner_mode_keeps_everything_open(tmp_path, monkeypatch) -> None:
    """Без `BILLING_JWT_SECRET` слой не вмешивается: это машина владельца."""
    monkeypatch.setattr(settings, "billing_jwt_secret", "")
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'owner.db'}", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _gen():
        async with factory() as s:
            yield s

    app = create_app()
    app.dependency_overrides[get_session] = _gen
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        res = await c.get("/api/me")
        assert res.status_code == 200
        assert res.json() == {
            "sso_enabled": False,
            "tenant_id": None,
            "email": "",
            "brand": "",
            "balance_micro": 0,
            "balance_credits": "0",
            "provisioned_now": False,
        }
    await engine.dispose()


async def test_cost_of_goods_is_not_a_tenant_surface(client) -> None:
    """Себестоимость — не данные клиента, а наши.

    `/api/llm-costs` отдаёт `cost_usd` по нодам и моделям. Данные при этом
    честно свои, деньги тоже, и путь выглядел безопасным — он и был у меня
    в списке разрешённого. Но клиент, увидевший $0.19 за клип, который стоит
    ему 0.57 кредита, узнаёт маржу ×3. Проверяя новый путь, спрашивай и это:
    не видно ли отсюда, сколько зарабатывает платформа.
    """
    headers = {"Authorization": f"Bearer {_token()}"}
    assert (await client.get("/api/projects/1/llm-costs", headers=headers)).status_code == 404
    assert (await client.get("/api/llm-costs/projects", headers=headers)).status_code == 404
