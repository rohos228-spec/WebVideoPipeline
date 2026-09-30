"""Сервис купонов и промокодов.

Реализует проверку и начисление кредитов по купонам.
Оперирует атомарно в транзакции с row-level lock (with_for_update),
чтобы исключить race conditions при повторной активации.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from loguru import logger
from sqlalchemy import select

from app.models import Coupon, CouponRedemption
from app.services.credit_ledger import topup


class CouponError(Exception):
    """Базовая ошибка работы с купонами."""


class CouponNotFoundError(CouponError):
    """Купон не найден или не активен."""


class CouponAlreadyUsedError(CouponError):
    """Купон уже исчерпал свой лимит использований."""


class CouponAlreadyRedeemedByUserError(CouponError):
    """Текущий пользователь уже активировал этот купон."""


async def redeem_coupon(
    session: Any,
    code: str,
    *,
    tenant_id: str,
    user_id: str | None = None,
) -> dict[str, Any]:
    """Активировать купон для арендатора.

    Возвращает словарь с результатом начисления.
    """
    clean_code = (code or "").strip().upper()
    if not clean_code or len(clean_code) < 3:
        raise CouponNotFoundError("Некорректный код купона")

    # Блокируем строку купона в транзакции
    stmt = select(Coupon).where(Coupon.code == clean_code).with_for_update()
    res = await session.execute(stmt)
    coupon = res.scalar_one_or_none()

    if coupon is None or not coupon.is_active:
        raise CouponNotFoundError("Купон не найден или недействителен")

    if coupon.expires_at is not None:
        if coupon.expires_at < datetime.now(UTC).replace(tzinfo=None):
            raise CouponNotFoundError("Срок действия купона истёк")

    if coupon.used_count >= coupon.max_uses:
        raise CouponAlreadyUsedError("Купон уже был использован")

    # Проверяем, не активировал ли уже этот арендатор данный купон
    check_stmt = (
        select(CouponRedemption.id)
        .where(
            CouponRedemption.coupon_id == coupon.id,
            CouponRedemption.tenant_id == tenant_id,
        )
        .limit(1)
    )
    already_redeemed = (await session.execute(check_stmt)).first()
    if already_redeemed is not None:
        raise CouponAlreadyRedeemedByUserError("Вы уже активировали этот купон ранее")

    # Фиксируем активацию
    coupon.used_count += 1

    redemption = CouponRedemption(
        id=str(uuid.uuid4()),
        coupon_id=coupon.id,
        tenant_id=tenant_id,
        user_id=user_id,
        amount_micro=coupon.amount_micro,
        redeemed_at=datetime.now(UTC).replace(tzinfo=None),
    )
    session.add(redemption)

    # Начисляем кредиты через credit_ledger
    from app.services.credits import format_credits

    credits_val = int(coupon.amount_micro // 1_000_000)
    memo = f"Активация купона {coupon.code}"
    new_balance_micro = await topup(
        session,
        tenant_id,
        coupon.amount_micro,
        memo=memo,
    )

    logger.info("купон {} активирован арендатором {} (+{} кр)", coupon.code, tenant_id, credits_val)

    return {
        "ok": True,
        "code": coupon.code,
        "credits_added": credits_val,
        "balance_micro": new_balance_micro,
        "balance_credits": format_credits(new_balance_micro),
        "message": f"Купон успешно активирован! Начислено {credits_val:,} кр.".replace(",", " "),
    }
