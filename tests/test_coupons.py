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
