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


async def create_coupon(
    session: Any,
    *,
    code: str,
    amount_credits: int | float,
    max_uses: int = 1,
    expires_at: datetime | None = None,
    is_active: bool = True,
) -> Coupon:
    """Создать новый купон / промокод."""
    clean_code = (code or "").strip().upper()
    if not clean_code or len(clean_code) < 3 or len(clean_code) > 32:
        raise ValueError("Код купона должен быть длиной от 3 до 32 символов")
    if amount_credits <= 0:
        raise ValueError("Количество кредитов должно быть больше нуля")
    if max_uses < 1:
        raise ValueError("Лимит использований должен быть не менее 1")

    existing = (
        await session.execute(select(Coupon.id).where(Coupon.code == clean_code))
    ).first()
    if existing is not None:
        raise ValueError(f"Купон с кодом {clean_code} уже существует")

    amount_micro = int(amount_credits * 1_000_000)
    coupon = Coupon(
        id=str(uuid.uuid4()),
        code=clean_code,
        amount_micro=amount_micro,
        max_uses=max_uses,
        used_count=0,
        is_active=is_active,
        expires_at=expires_at,
    )
    session.add(coupon)
    await session.flush()
    return coupon


async def list_coupons(
    session: Any,
    *,
    limit: int = 50,
    offset: int = 0,
    active_only: bool = False,
) -> list[dict[str, Any]]:
    """Список купонов для панели управления / CLI."""
    from app.services.credits import format_credits

    stmt = select(Coupon).order_by(Coupon.created_at.desc())
    if active_only:
        stmt = stmt.where(Coupon.is_active.is_(True))
    stmt = stmt.limit(max(1, min(limit, 200))).offset(max(0, offset))

    res = await session.execute(stmt)
    coupons = res.scalars().all()

    return [
        {
            "id": c.id,
            "code": c.code,
            "credits": c.amount_micro // 1_000_000,
            "balance_credits": format_credits(c.amount_micro),
            "max_uses": c.max_uses,
            "used_count": c.used_count,
            "is_active": c.is_active,
            "created_at": c.created_at.isoformat() if c.created_at else None,
            "expires_at": c.expires_at.isoformat() if c.expires_at else None,
        }
        for c in coupons
    ]


async def deactivate_coupon(session: Any, code: str) -> bool:
    """Деактивировать купон по коду."""
    clean_code = (code or "").strip().upper()
    stmt = select(Coupon).where(Coupon.code == clean_code).with_for_update()
    coupon = (await session.execute(stmt)).scalar_one_or_none()
    if coupon is None:
        return False
    coupon.is_active = False
    await session.flush()
    return True
