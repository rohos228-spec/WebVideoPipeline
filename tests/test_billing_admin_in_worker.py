"""Касса узнаёт админа и в воркере — не только в HTTP-запросе.

`step_billing` не тарифицирует админа по `current_is_admin()`, но этот флаг
живёт в личности запроса. Воркер идёт без запроса, с одним `tenant_id`, и
для него админ был неотличим от клиента с нулём на счету: в интерфейсе «∞»,
в журнале — «шаг video ждёт пополнения — нужно 11.88 кр, доступно 0 кр».
Первый живой прогон встал ровно здесь: все дешёвые шаги прошли бесплатным
уровнем, а дорогое видео — нет.

Проверяется в условиях воркера: личности нет, арендатор задан. Админ —
без резерва; участник с нулём — по-прежнему упирается в кассу, иначе правка
означала бы «касса выключена для всех».
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from app.services import step_billing as sb
from app.services.studio_users import forget_admin_cache
from app.services.tenant import tenant_scope
from tests import accounts_harness as ah


@pytest_asyncio.fixture
async def env(tmp_path, monkeypatch):
    ah.configure(monkeypatch)
    engine, factory = await ah.make_engine(tmp_path / "bill.db")
    ah.bind_identity_session(monkeypatch, factory)
    # Касса открывает свои сессии через session_scope — подменяем на тестовую базу.
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _scope():
        async with factory() as s:
            yield s
            await s.commit()

    monkeypatch.setattr(sb, "session_scope", _scope)
    admin = await ah.make_account(factory, email="boss@studio.local", role="admin")
    member = await ah.make_account(factory, email="user@studio.local", role="member")
    forget_admin_cache()
    yield {"admin": admin, "member": member}
    forget_admin_cache()
    await engine.dispose()


class _Project:
    id = 1
    slug = "rolik"
    meta: dict = {}


@pytest.mark.asyncio
async def test_admin_tenant_is_not_billed_without_request_identity(env) -> None:
    with tenant_scope(env["admin"].user_id):
        async with sb.step_billing(_Project(), "video", frames=12) as bill:
            assert bill.tenant_id is None and bill.hold_id is None, "админ в воркере попал под кассу"


@pytest.mark.asyncio
async def test_member_tenant_with_zero_balance_still_hits_the_till(env) -> None:
    from app.services.credit_ledger import InsufficientCredits
    from app.services.free_tier import FreeTierExhausted

    with tenant_scope(env["member"].user_id):
        with pytest.raises((InsufficientCredits, FreeTierExhausted)):
            async with sb.step_billing(_Project(), "video", frames=12):
                pass
