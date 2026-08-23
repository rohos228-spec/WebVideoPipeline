"""Первый вход клиента: завести счёт, положить стартовый баланс. Один раз.

Регистрации у студии нет — она есть в биллинге (`docs/SAAS-PIVOT.md` §4.1).
Значит момент «этот арендатор появился» наступает не по кнопке «создать
аккаунт», а по первому запросу с валидным токеном. Отсюда два требования, и
оба неочевидны:

**Идемпотентность.** Токен живёт неделю и приходит с каждым запросом. Функция
обязана быть безразлична к тому, зовут её первый раз или тысячный: подарок
выдаётся ровно однажды, и признаком «уже выдавали» служит не флаг в отдельной
таблице, а сам леджер — проводка `promo` с этим арендатором. Одно место
правды по деньгам, как и договорено в §5.3.

**Стартовый баланс по умолчанию — ноль.** Бесплатный уровень (§5.7) сделан не
кредитами, а промо-проводками с нулевой дельтой: подарок там — раскадровка
себестоимостью $0.97, а не деньги на счету. Живые кредиты на входе включаются
настройкой `TENANT_START_CREDITS` и означают ровно то, что написано, — подарок
деньгами, который клиент может унести в генерацию видео.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select

from app.models import CreditAccount, CreditEntry

#: Метка стартового подарка в леджере. По ней же проверяется «уже выдавали».
WELCOME_MEMO = "стартовый баланс при первом входе"


@dataclass(frozen=True)
class Provisioned:
    """Итог первого входа: счёт, остаток и был ли выдан подарок сейчас."""

    tenant_id: str
    balance_micro: int
    created: bool
    welcome_micro: int = 0


async def ensure_tenant(session: Any, tenant_id: str) -> Provisioned:
    """Счёт арендатора существует, подарок выдан не более одного раза."""
    from app.services import credit_ledger as cl
    from app.services.credits import price_micro
    from app.settings import settings

    existed = await session.get(CreditAccount, tenant_id) is not None
    await cl.ensure_account(session, tenant_id)

    welcome = 0
    start_credits = float(getattr(settings, "tenant_start_credits", 0.0) or 0.0)
    if start_credits > 0 and not await _welcome_was_given(session, tenant_id):
        # Подарок задан В КРЕДИТАХ, а не в себестоимости: это цена, которую
        # клиент может потратить, поэтому маржа к нему не применяется.
        welcome = price_micro(start_credits, with_margin=False)
        if welcome > 0:
            await cl.topup(session, tenant_id, welcome, memo=WELCOME_MEMO)

    return Provisioned(
        tenant_id=tenant_id,
        balance_micro=await cl.balance_micro(session, tenant_id),
        created=not existed,
        welcome_micro=welcome,
    )


async def _welcome_was_given(session: Any, tenant_id: str) -> bool:
    """Подарок ищется в леджере, а не в отдельном флаге.

    Флаг разошёлся бы с проводками при первой же ручной правке, и разошёлся
    бы молча. Леджер — единственный источник правды по деньгам (§5.3).
    """
    found = await session.execute(
        select(func.count())
        .select_from(CreditEntry)
        .where(
            CreditEntry.tenant_id == tenant_id,
            CreditEntry.kind == "topup",
            CreditEntry.memo == WELCOME_MEMO,
        )
    )
    return bool(found.scalar_one())
