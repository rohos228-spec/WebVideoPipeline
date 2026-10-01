"""Тесты купонов: проверка активации, лимитов и баланса."""

from __future__ import annotations

import uuid

import pytest

from app.db import session_scope
from app.models import Coupon
from app.services import credit_ledger as cl
from app.services.coupon_service import (
    CouponAlreadyUsedError,
    CouponNotFoundError,
    redeem_coupon,
)


def _tenant() -> str:
    return str(uuid.uuid4())


async def test_redeem_valid_coupon() -> None:
    tenant = _tenant()
    code = f"TEST{uuid.uuid4().hex[:6].upper()}"

    async with session_scope() as s:
        coupon = Coupon(
            id=str(uuid.uuid4()),
            code=code,
            amount_micro=500_000_000,
            max_uses=1,
            used_count=0,
            is_active=True,
        )
        s.add(coupon)
        await s.flush()

    async with session_scope() as s:
        res = await redeem_coupon(s, code, tenant_id=tenant)
        assert res["ok"] is True
        assert res["code"] == code
        assert res["credits_added"] == 500
        assert res["balance_micro"] == 500_000_000

        # Баланс арендатора проверен через ledger
        bal = await cl.balance_micro(s, tenant)
        assert bal == 500_000_000


async def test_redeem_coupon_once_only() -> None:
    tenant1 = _tenant()
    tenant2 = _tenant()
    code = f"ONCE{uuid.uuid4().hex[:6].upper()}"

    async with session_scope() as s:
        coupon = Coupon(
            id=str(uuid.uuid4()),
            code=code,
            amount_micro=2_500_000_000,
            max_uses=1,
            used_count=0,
            is_active=True,
        )
        s.add(coupon)
        await s.flush()

    # Первый арендатор успешно активирует
    async with session_scope() as s:
        res = await redeem_coupon(s, code, tenant_id=tenant1)
        assert res["ok"] is True
        assert res["credits_added"] == 2500

    # Тот же арендатор пытается снова
    async with session_scope() as s:
        with pytest.raises(CouponAlreadyUsedError):
            await redeem_coupon(s, code, tenant_id=tenant1)

    # Другой арендатор пытается применить тот же купон
    async with session_scope() as s:
        with pytest.raises(CouponAlreadyUsedError):
            await redeem_coupon(s, code, tenant_id=tenant2)


async def test_redeem_invalid_or_inactive_coupon() -> None:
    tenant = _tenant()

    async with session_scope() as s:
        with pytest.raises(CouponNotFoundError):
            await redeem_coupon(s, "NON_EXISTENT_CODE", tenant_id=tenant)

    code_inactive = f"OFF{uuid.uuid4().hex[:6].upper()}"
    async with session_scope() as s:
        coupon = Coupon(
            id=str(uuid.uuid4()),
            code=code_inactive,
            amount_micro=500_000_000,
            max_uses=1,
            used_count=0,
            is_active=False,
        )
        s.add(coupon)
        await s.flush()

    async with session_scope() as s:
        with pytest.raises(CouponNotFoundError):
            await redeem_coupon(s, code_inactive, tenant_id=tenant)


async def test_create_list_and_deactivate_coupon() -> None:
    from app.services.coupon_service import (
        create_coupon,
        deactivate_coupon,
        list_coupons,
    )

    code = f"NEW{uuid.uuid4().hex[:6].upper()}"
    tenant = _tenant()

    async with session_scope() as s:
        created = await create_coupon(s, code=code, amount_credits=750, max_uses=5)
        assert created.code == code
        assert created.amount_micro == 750_000_000

    # Проверяем листинг
    async with session_scope() as s:
        items = await list_coupons(s, limit=10)
        found = next((item for item in items if item["code"] == code), None)
        assert found is not None
        assert found["credits"] == 750
        assert found["is_active"] is True

    # Активируем с пробелами и нижним регистром
    async with session_scope() as s:
        res = await redeem_coupon(s, f"  {code.lower()}  ", tenant_id=tenant)
        assert res["ok"] is True
        assert res["credits_added"] == 750

    # Деактивируем
    async with session_scope() as s:
        ok = await deactivate_coupon(s, code)
        assert ok is True

    # Пытаемся активировать другим арендатором — купон выключен
    tenant2 = _tenant()
    async with session_scope() as s:
        with pytest.raises(CouponNotFoundError):
            await redeem_coupon(s, code, tenant_id=tenant2)


async def test_multi_use_coupon_limits() -> None:
    from app.services.coupon_service import (
        CouponAlreadyRedeemedByUserError,
        create_coupon,
    )

    code = f"MULTI{uuid.uuid4().hex[:6].upper()}"
    t1, t2, t3 = _tenant(), _tenant(), _tenant()

    async with session_scope() as s:
        await create_coupon(s, code=code, amount_credits=100, max_uses=2)

    # t1 активирует — успех
    async with session_scope() as s:
        r1 = await redeem_coupon(s, code, tenant_id=t1)
        assert r1["ok"] is True

    # t1 пытается повторно — CouponAlreadyRedeemedByUserError
    async with session_scope() as s:
        with pytest.raises(CouponAlreadyRedeemedByUserError):
            await redeem_coupon(s, code, tenant_id=t1)

    # t2 активирует — успех (второе использование)
    async with session_scope() as s:
        r2 = await redeem_coupon(s, code, tenant_id=t2)
        assert r2["ok"] is True

    # t3 пытается активировать — лимит исчерпан (CouponAlreadyUsedError)
    async with session_scope() as s:
        with pytest.raises(CouponAlreadyUsedError):
            await redeem_coupon(s, code, tenant_id=t3)


async def test_admin_coupon_endpoints() -> None:
    from fastapi import HTTPException

    from app.services.studio_auth import ROLE_ADMIN, ROLE_MEMBER, StudioIdentity, set_identity
    from app.web.routers.billing import (
        CreateCouponIn,
        DeactivateCouponIn,
        create_coupon_endpoint,
        deactivate_coupon_endpoint,
        list_coupons_endpoint,
    )

    # 1. Member пытается создать купон -> 403 Forbidden
    member_ident = StudioIdentity(user_id=str(uuid.uuid4()), email="user@test.ru", role=ROLE_MEMBER)
    set_identity(member_ident)
    async with session_scope() as s:
        with pytest.raises(HTTPException) as exc:
            await create_coupon_endpoint(CreateCouponIn(credits=500), session=s)
        assert exc.value.status_code == 403

    # 2. Admin создаёт купон -> успех
    admin_ident = StudioIdentity(user_id=str(uuid.uuid4()), email="admin@test.ru", role=ROLE_ADMIN)
    set_identity(admin_ident)
    async with session_scope() as s:
        res = await create_coupon_endpoint(CreateCouponIn(credits=1000, max_uses=10), session=s)
        assert res["ok"] is True
        assert res["credits"] == 1000
        code = res["code"]

        # Admin запрашивает список купонов
        coupons = await list_coupons_endpoint(session=s)
        assert any(c["code"] == code for c in coupons)

        # Admin деактивирует купон
        deact = await deactivate_coupon_endpoint(DeactivateCouponIn(code=code), session=s)
        assert deact["ok"] is True

    set_identity(None)
