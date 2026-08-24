"""Мост от кассы биллинга к леджеру студии.

Платёж принимает биллинг — YooKassa, чек по 422-ФЗ, проводка в рублях.
Кредиты, которыми платят за шаги, лежат в `credit_accounts` студии. Между
этими двумя фактами до сих пор не было ничего: клиент мог заплатить, а баланс
в студии остался бы нулевым.

Проверяется то, на чём такие мосты ломаются: доступ, повтор вебхука и
попадание денег нужному арендатору.
"""

from __future__ import annotations

import uuid

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base
from app.settings import settings
from app.web.api import create_app
from app.web.deps import get_session

# Только ASCII: секрет едет в HTTP-заголовке, а туда кириллица не влезает.
SECRET = "s2s-secret-at-least-32-bytes-long-0123456789"
#: Секрет арендаторских токенов — другой. Ручка пополнения не должна
#: открываться клиентским токеном, и это проверяется отдельно.
JWT_SECRET = "jwt-secret-at-least-32-bytes-long-0123456789"


@pytest_asyncio.fixture
async def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "billing_webhook_secret", SECRET)
    monkeypatch.setattr(settings, "allow_unisolated_tenants", True)
    # SaaS, а не режим владельца. Первая редакция теста этого не делала, и
    # ручка проверялась там, где слой личности выключен: в бою middleware
    # резал её до роутера, и пополнение не работало вовсе. Поймалось живым
    # прогоном — тест обязан гонять её в том режиме, в котором она живёт.
    monkeypatch.setattr(settings, "billing_jwt_secret", JWT_SECRET)
    monkeypatch.setattr(settings, "studio_brand", "multik")
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'hook.db'}", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _gen():
        async with factory() as s:
            yield s

    app = create_app()
    app.dependency_overrides[get_session] = _gen
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        c.factory = factory  # type: ignore[attr-defined]
        yield c
    await engine.dispose()


def _body(tenant: str, credits: float = 10.0, payment_id: str = "pay-1") -> dict:
    return {"tenant_id": tenant, "credits": credits, "payment_id": payment_id}


async def test_payment_lands_on_the_balance(client) -> None:
    """Основное: заплатил — увидел кредиты."""
    tenant = str(uuid.uuid4())
    res = await client.post(
        "/api/billing-hook/topup",
        json=_body(tenant, 10.0),
        headers={"X-Billing-Secret": SECRET},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["applied"] is True
    assert body["balance_micro"] == 10 * 10**6
    assert body["balance_credits"] == "10.00"


async def test_repeated_webhook_does_not_pay_twice(client) -> None:
    """Платёжные провайдеры повторяют вебхуки: сеть моргнула, ответ не дошёл.

    Начисленное дважды пополнение — это подарок за счёт платформы, который
    никто не заметит до сверки. Признак повтора — идентификатор платежа.
    """
    tenant = str(uuid.uuid4())
    headers = {"X-Billing-Secret": SECRET}
    first = (await client.post("/api/billing-hook/topup", json=_body(tenant), headers=headers)).json()
    second = (await client.post("/api/billing-hook/topup", json=_body(tenant), headers=headers)).json()

    assert first["applied"] is True
    assert second["applied"] is False
    assert second["balance_micro"] == first["balance_micro"], "повтор начислил второй раз"


async def test_a_different_payment_is_a_different_top_up(client) -> None:
    """Идемпотентность по платежу, а не по арендатору: второй платёж — деньги."""
    tenant = str(uuid.uuid4())
    headers = {"X-Billing-Secret": SECRET}
    await client.post("/api/billing-hook/topup", json=_body(tenant, 10.0, "pay-1"), headers=headers)
    second = (
        await client.post("/api/billing-hook/topup", json=_body(tenant, 5.0, "pay-2"), headers=headers)
    ).json()
    assert second["applied"] is True
    assert second["balance_micro"] == 15 * 10**6


async def test_without_the_secret_nobody_gets_credits(client) -> None:
    """Токеном клиента баланс не пополняется.

    Секрет здесь отдельный от `BILLING_JWT_SECRET` именно поэтому: приём
    пополнения по клиентскому токену означал бы, что клиент пополняет себе
    баланс сам.
    """
    tenant = str(uuid.uuid4())
    assert (await client.post("/api/billing-hook/topup", json=_body(tenant))).status_code == 401
    wrong = await client.post(
        "/api/billing-hook/topup", json=_body(tenant), headers={"X-Billing-Secret": "wrong-secret"}
    )
    assert wrong.status_code == 401


async def test_the_hook_is_off_without_a_configured_secret(client, monkeypatch) -> None:
    """Не настроен секрет — ручка закрыта, а не открыта всем.

    Пустой секрет, сравниваемый с пустым заголовком, дал бы совпадение — и
    пополнение стало бы публичным.
    """
    monkeypatch.setattr(settings, "billing_webhook_secret", "")
    res = await client.post(
        "/api/billing-hook/topup",
        json=_body(str(uuid.uuid4())),
        headers={"X-Billing-Secret": ""},
    )
    assert res.status_code == 401


async def test_money_lands_on_the_named_tenant(client) -> None:
    """Деньги приходят тому, кто заплатил, а не тому, кто последний ходил."""
    from app.services import credit_ledger as cl
    from app.services.tenant import tenant_scope

    alice, bob = str(uuid.uuid4()), str(uuid.uuid4())
    headers = {"X-Billing-Secret": SECRET}
    await client.post("/api/billing-hook/topup", json=_body(alice, 7.0, "p-a"), headers=headers)

    async with client.factory() as s:  # type: ignore[attr-defined]
        with tenant_scope(alice):
            assert await cl.balance_micro(s, alice) == 7 * 10**6
        with tenant_scope(bob):
            assert await cl.balance_micro(s, bob) == 0


async def test_broken_tenant_id_is_refused(client) -> None:
    """`tenant_id` уезжает в границу изоляции — мусор туда пускать нельзя."""
    res = await client.post(
        "/api/billing-hook/topup",
        json={"tenant_id": "не-uuid", "credits": 1.0, "payment_id": "p"},
        headers={"X-Billing-Secret": SECRET},
    )
    assert res.status_code == 400


async def test_a_tenant_token_does_not_open_the_hook(client) -> None:
    """Ручка публична для слоя личности и закрыта своим секретом.

    Это два разных вопроса, и их легко перепутать. Слой личности её
    пропускает — иначе вебхук был бы недостижим: у биллинга нет и не может
    быть токена арендатора, платёж принимает он, а не клиент. Значит
    проверять доступ обязана сама ручка, и токен клиента ей не подходит.
    """
    import time

    import jwt

    from app.web.identity import path_requires_identity

    # Слой личности её не режет — иначе пополнение не работало бы вовсе.
    assert not path_requires_identity("/api/billing-hook/topup")

    tenant_token = jwt.encode(
        {
            "sub": str(uuid.uuid4()),
            "brand": "multik",
            "iat": int(time.time()),
            "exp": int(time.time()) + 600,
        },
        JWT_SECRET,
        algorithm="HS256",
    )
    refused = await client.post(
        "/api/billing-hook/topup",
        json=_body(str(uuid.uuid4())),
        headers={"Authorization": f"Bearer {tenant_token}"},
    )
    assert refused.status_code == 401, "клиентский токен открыл пополнение"
