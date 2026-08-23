"""Леджер: холд → списание → возврат (docs/SAAS-PIVOT.md §5.4).

Правда о деньгах — проводки. `credit_accounts.balance_micro` их кэш, и
кэшируется в нём **доступный** остаток: то, что уже зарезервировано под
идущий шаг, из него вычтено. Отсюда инвариант, который проверяется тестом:

    доступно = Σ delta_micro − Σ amount_micro открытых холдов

**Холд — не проводка.** Резерв ничего не списывает: он лишь запрещает
потратить те же деньги дважды. Поэтому снятие резерва тоже не проводка —
запись `kind='release'` пишется с нулевой дельтой, как след в журнале. Если
бы возврат создавал проводку `+amount`, деньги удваивались бы: сумма
дельт выросла бы, а сумма холдов упала — доступный остаток скакнул бы на два
резерва вместо одного. Ровно тот класс ошибки, ради которого леджер и
заводят.

**Списание возвращает излишек той же операцией.** Резервируется p90, тратится
факт; разница возвращается в момент закрытия холда, а не отдельной задачей,
которая может не прийти.

**Перерасход не прячется.** Если фактическая стоимость превысила резерв,
остаток уходит в минус и пишется предупреждение. Обнулить его молча значило
бы подарить работу; отказаться списывать — соврать в отчёте о марже.

**Гонки закрыты одним UPDATE.** Проверка «хватает ли денег» и списание — одно
условное выражение с `RETURNING`, а не «прочитали, подумали, записали».
Два параллельных шага одного арендатора иначе увели бы баланс в минус на
ровном месте.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from loguru import logger
from sqlalchemy import select, update

from app.models import CreditAccount, CreditEntry, CreditHold
from app.services.credits import format_credits, margin, price_micro

#: Сколько живёт резерв, если шаг не закрыл его сам. Страховка от зависшего
#: шага: деньги не зависают никогда, даже если воркер умер посреди работы.
DEFAULT_HOLD_TTL = timedelta(hours=6)

HELD = "held"
SETTLED = "settled"
RELEASED = "released"
EXPIRED = "expired"


class InsufficientCredits(RuntimeError):
    """Не хватает доступного остатка на резерв под шаг."""

    def __init__(self, need_micro: int, have_micro: int):
        self.need_micro = need_micro
        self.have_micro = have_micro
        super().__init__(
            f"нужно {format_credits(need_micro, rounding='up')} кр, доступно {format_credits(have_micro)} кр"
        )


class UnknownHold(RuntimeError):
    """Холда нет или он уже закрыт."""


@dataclass(frozen=True)
class Settlement:
    """Итог списания: сколько списано и сколько вернулось."""

    hold_id: str
    charged_micro: int
    returned_micro: int
    overrun_micro: int

    @property
    def overran(self) -> bool:
        return self.overrun_micro > 0


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


async def ensure_account(session: Any, tenant_id: str) -> CreditAccount:
    """Счёт арендатора, создавая при первом обращении."""
    account = await session.get(CreditAccount, tenant_id)
    if account is None:
        account = CreditAccount(tenant_id=tenant_id, balance_micro=0, updated_at=_now())
        session.add(account)
        await session.flush()
    return account


async def balance_micro(session: Any, tenant_id: str) -> int:
    """Доступный остаток: резервы под идущие шаги уже вычтены."""
    row = await session.get(CreditAccount, tenant_id)
    return int(row.balance_micro) if row else 0


async def topup(
    session: Any,
    tenant_id: str,
    amount_micro: int,
    *,
    memo: str = "",
) -> int:
    """Пополнение. Возвращает новый доступный остаток."""
    if amount_micro <= 0:
        raise ValueError("пополнение должно быть положительным")
    await ensure_account(session, tenant_id)
    session.add(
        CreditEntry(
            id=str(uuid.uuid4()),
            tenant_id=tenant_id,
            hold_id=None,
            delta_micro=int(amount_micro),
            kind="topup",
            ref_ids=[],
            memo=memo,
            created_at=_now(),
        )
    )
    await session.execute(
        update(CreditAccount)
        .where(CreditAccount.tenant_id == tenant_id)
        .values(balance_micro=CreditAccount.balance_micro + int(amount_micro), updated_at=_now())
    )
    await session.flush()
    return await balance_micro(session, tenant_id)


async def open_hold(
    session: Any,
    tenant_id: str,
    *,
    project_id: int,
    step_code: str,
    amount_micro: int,
    node_key: str = "",
    ttl: timedelta = DEFAULT_HOLD_TTL,
) -> CreditHold:
    """Зарезервировать под шаг. Бросает ``InsufficientCredits``, если мало.

    Проверка остатка и его уменьшение — один UPDATE с условием: между
    «хватает» и «списали» не должно быть промежутка, в который влезет
    параллельный шаг того же арендатора.
    """
    amount = int(amount_micro)
    if amount < 0:
        raise ValueError("резерв не может быть отрицательным")
    await ensure_account(session, tenant_id)

    if amount:
        result = await session.execute(
            update(CreditAccount)
            .where(
                CreditAccount.tenant_id == tenant_id,
                CreditAccount.balance_micro >= amount,
            )
            .values(balance_micro=CreditAccount.balance_micro - amount, updated_at=_now())
            .returning(CreditAccount.balance_micro)
        )
        if result.scalar_one_or_none() is None:
            have = await balance_micro(session, tenant_id)
            raise InsufficientCredits(amount, have)

    hold = CreditHold(
        id=str(uuid.uuid4()),
        tenant_id=tenant_id,
        project_id=int(project_id),
        step_code=step_code,
        node_key=node_key,
        amount_micro=amount,
        state=HELD,
        expires_at=_now() + ttl,
        created_at=_now(),
    )
    session.add(hold)
    await session.flush()
    return hold


async def settle_hold(
    session: Any,
    hold_id: str,
    *,
    cost_usd: float | Decimal,
    ref_table: str = "",
    ref_ids: list[int] | None = None,
    memo: str = "",
) -> Settlement:
    """Закрыть резерв по факту: списать цену, вернуть излишек.

    ``cost_usd`` — фактическая себестоимость шага, сумма `cost_usd` вызовов
    из журналов. Цена считается из неё с текущей маржой; и то и другое
    пишется в проводку, чтобы через год можно было ответить, почему списано
    именно столько.
    """
    hold = await _open_hold_row(session, hold_id)
    charged = price_micro(cost_usd)
    reserved = int(hold.amount_micro)
    returned = max(0, reserved - charged)
    overrun = max(0, charged - reserved)

    session.add(
        CreditEntry(
            id=str(uuid.uuid4()),
            tenant_id=hold.tenant_id,
            hold_id=hold.id,
            delta_micro=-charged,
            kind="settle",
            cost_usd=Decimal(str(cost_usd)),
            margin=margin(),
            ref_table=ref_table,
            ref_ids=list(ref_ids or []),
            memo=memo,
            created_at=_now(),
        )
    )
    # Возвращаем весь резерв и списываем факт — одной операцией, чтобы между
    # ними не мог вклиниться параллельный холд.
    await session.execute(
        update(CreditAccount)
        .where(CreditAccount.tenant_id == hold.tenant_id)
        .values(
            balance_micro=CreditAccount.balance_micro + reserved - charged,
            updated_at=_now(),
        )
    )
    hold.state = SETTLED
    await session.flush()

    if overrun:
        logger.warning(
            "credits: перерасход по холду {} — резерв {} кр, списано {} кр",
            hold_id,
            format_credits(reserved),
            format_credits(charged, rounding="up"),
        )
    return Settlement(hold.id, charged, returned, overrun)


async def release_hold(session: Any, hold_id: str, *, memo: str = "", expired: bool = False) -> int:
    """Снять резерв целиком: шаг упал, клиент не платит ни за одну попытку.

    Возвращает освобождённую сумму. Проводка пишется с нулевой дельтой —
    это след в журнале, а не движение денег: сам резерв проводкой не был.
    """
    hold = await _open_hold_row(session, hold_id)
    amount = int(hold.amount_micro)

    session.add(
        CreditEntry(
            id=str(uuid.uuid4()),
            tenant_id=hold.tenant_id,
            hold_id=hold.id,
            delta_micro=0,
            kind="release",
            ref_ids=[],
            memo=memo or ("истёк срок резерва" if expired else ""),
            created_at=_now(),
        )
    )
    await session.execute(
        update(CreditAccount)
        .where(CreditAccount.tenant_id == hold.tenant_id)
        .values(balance_micro=CreditAccount.balance_micro + amount, updated_at=_now())
    )
    hold.state = EXPIRED if expired else RELEASED
    await session.flush()
    return amount


async def expire_stale(session: Any, *, now: datetime | None = None) -> list[str]:
    """Освободить резервы, которые никто не закрыл. Возвращает их id.

    Зависший шаг не должен превращаться в замороженные деньги: воркер мог
    умереть, машина — уйти в перезагрузку. Ходит фоновой задачей.
    """
    moment = now or _now()
    stale = (
        (
            await session.execute(
                select(CreditHold).where(
                    CreditHold.state == HELD,
                    CreditHold.expires_at < moment,
                )
            )
        )
        .scalars()
        .all()
    )
    released = []
    for hold in stale:
        await release_hold(session, hold.id, expired=True)
        released.append(hold.id)
    if released:
        logger.warning("credits: освобождено зависших резервов — {}", len(released))
    return released


async def reconcile(session: Any, tenant_id: str) -> tuple[int, int]:
    """``(остаток в счёте, остаток по проводкам)``. Расхождение — авария.

    Считается независимо от кэша: сумма дельт минус сумма открытых резервов.
    Ночная сверка и тест инварианта смотрят сюда.
    """
    from sqlalchemy import func

    entries = (
        await session.execute(
            select(func.coalesce(func.sum(CreditEntry.delta_micro), 0)).where(
                CreditEntry.tenant_id == tenant_id
            )
        )
    ).scalar_one()
    held = (
        await session.execute(
            select(func.coalesce(func.sum(CreditHold.amount_micro), 0)).where(
                CreditHold.tenant_id == tenant_id,
                CreditHold.state == HELD,
            )
        )
    ).scalar_one()
    cached = await balance_micro(session, tenant_id)
    return (int(cached), int(entries) - int(held))


async def _open_hold_row(session: Any, hold_id: str) -> CreditHold:
    hold = await session.get(CreditHold, hold_id)
    if hold is None:
        raise UnknownHold(f"холд {hold_id} не найден")
    if hold.state != HELD:
        raise UnknownHold(f"холд {hold_id} уже закрыт: {hold.state}")
    return hold
