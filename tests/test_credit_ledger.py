"""Леджер кредитов: холд → списание → возврат.

Проверяется инвариант, а не вызовы: доступный остаток обязан сходиться с
проводками в любой момент. Расхождение здесь — не баг отображения, а
потерянные или подаренные деньги.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest

from app.db import session_scope
from app.services import credit_ledger as cl
from app.services.credits import price_micro


def _tenant() -> str:
    return str(uuid.uuid4())


async def _assert_consistent(session, tenant: str) -> int:
    cached, computed = await cl.reconcile(session, tenant)
    assert cached == computed, f"кэш баланса разошёлся с проводками: {cached} != {computed}"
    return cached


async def test_topup_then_hold_then_settle_returns_the_rest() -> None:
    """Резервируем p90, тратим факт, излишек возвращается той же операцией."""
    tenant = _tenant()
    async with session_scope() as s:
        await cl.topup(s, tenant, 20 * 10**6, memo="стартовый пакет")
        await _assert_consistent(s, tenant)

        # Шаг «Видео»: резерв по p90 $5.00, факт вышел $4.56.
        hold = await cl.open_hold(
            s, tenant, project_id=1, step_code="video", amount_micro=price_micro(5.00)
        )
        after_hold = await _assert_consistent(s, tenant)
        assert after_hold == 20 * 10**6 - price_micro(5.00)

        result = await cl.settle_hold(s, hold.id, cost_usd=4.56, ref_table="media_calls", ref_ids=[1, 2])
        assert result.charged_micro == price_micro(4.56)
        assert result.returned_micro == price_micro(5.00) - price_micro(4.56)
        assert not result.overran

        final = await _assert_consistent(s, tenant)
        assert final == 20 * 10**6 - price_micro(4.56)


async def test_failed_step_costs_nothing() -> None:
    """Шаг упал — клиент не платит ни за одну попытку (§5.4 п.5)."""
    tenant = _tenant()
    async with session_scope() as s:
        await cl.topup(s, tenant, 10 * 10**6)
        hold = await cl.open_hold(s, tenant, project_id=1, step_code="img", amount_micro=500_000)
        await cl.release_hold(s, hold.id, memo="провайдер отклонил сюжет")
        assert await _assert_consistent(s, tenant) == 10 * 10**6


async def test_release_does_not_double_the_money() -> None:
    """Снятие резерва — след в журнале, а не проводка.

    Если бы возврат создавал проводку `+amount`, сумма дельт выросла бы, а
    сумма холдов упала: остаток скакнул бы на два резерва вместо одного.
    """
    tenant = _tenant()
    async with session_scope() as s:
        await cl.topup(s, tenant, 5 * 10**6)
        hold = await cl.open_hold(s, tenant, project_id=1, step_code="img", amount_micro=1_000_000)
        await cl.release_hold(s, hold.id)
        assert await cl.balance_micro(s, tenant) == 5 * 10**6
        await _assert_consistent(s, tenant)


async def test_hold_beyond_balance_is_refused() -> None:
    tenant = _tenant()
    async with session_scope() as s:
        await cl.topup(s, tenant, 1_000_000)
        with pytest.raises(cl.InsufficientCredits):
            await cl.open_hold(s, tenant, project_id=1, step_code="video", amount_micro=2_000_000)
        # Отказ не тронул остаток.
        assert await _assert_consistent(s, tenant) == 1_000_000


async def test_two_parallel_holds_cannot_share_the_same_money() -> None:
    """Проверка остатка и его списание — одна операция.

    Иначе два шага одного арендатора зарезервировали бы одни и те же деньги
    дважды и увели баланс в минус.
    """
    tenant = _tenant()
    async with session_scope() as s:
        await cl.topup(s, tenant, 1_500_000)
        await cl.open_hold(s, tenant, project_id=1, step_code="img", amount_micro=1_000_000)
        with pytest.raises(cl.InsufficientCredits):
            await cl.open_hold(s, tenant, project_id=2, step_code="img", amount_micro=1_000_000)
        assert await _assert_consistent(s, tenant) == 500_000


async def test_overrun_is_charged_not_hidden() -> None:
    """Факт превысил резерв — списывается факт, остаток уходит в минус.

    Обнулить перерасход значило бы подарить работу; отказаться списывать —
    соврать в отчёте о марже.
    """
    tenant = _tenant()
    async with session_scope() as s:
        await cl.topup(s, tenant, 1_000_000)
        hold = await cl.open_hold(s, tenant, project_id=1, step_code="video", amount_micro=300_000)
        result = await cl.settle_hold(s, hold.id, cost_usd=0.50)  # 1.5 кредита
        assert result.overran
        assert result.charged_micro == price_micro(0.50)
        balance = await _assert_consistent(s, tenant)
        assert balance == 1_000_000 - price_micro(0.50)


async def test_stale_hold_is_released_by_ttl() -> None:
    """Зависший шаг не превращается в замороженные деньги."""
    tenant = _tenant()
    async with session_scope() as s:
        await cl.topup(s, tenant, 5 * 10**6)
        hold = await cl.open_hold(
            s,
            tenant,
            project_id=1,
            step_code="video",
            amount_micro=2 * 10**6,
            ttl=timedelta(seconds=-1),  # уже просрочен
        )
        released = await cl.expire_stale(s)
        assert hold.id in released
        assert await _assert_consistent(s, tenant) == 5 * 10**6
        assert (await s.get(cl.CreditHold, hold.id)).state == cl.EXPIRED


async def test_hold_cannot_be_closed_twice() -> None:
    tenant = _tenant()
    async with session_scope() as s:
        await cl.topup(s, tenant, 5 * 10**6)
        hold = await cl.open_hold(s, tenant, project_id=1, step_code="img", amount_micro=10_000)
        await cl.release_hold(s, hold.id)
        with pytest.raises(cl.UnknownHold):
            await cl.release_hold(s, hold.id)
        with pytest.raises(cl.UnknownHold):
            await cl.settle_hold(s, hold.id, cost_usd=0.01)


async def test_settle_records_cost_and_margin_for_audit() -> None:
    """По каждой списанной доле видно, из чего она сложилась (§5.3)."""
    from sqlalchemy import select

    from app.models import CreditEntry

    tenant = _tenant()
    async with session_scope() as s:
        await cl.topup(s, tenant, 10 * 10**6)
        hold = await cl.open_hold(s, tenant, project_id=7, step_code="video", amount_micro=10**6)
        await cl.settle_hold(
            s, hold.id, cost_usd=0.19, ref_table="media_calls", ref_ids=[11, 12, 13]
        )
        entry = (
            await s.execute(
                select(CreditEntry).where(
                    CreditEntry.tenant_id == tenant, CreditEntry.kind == "settle"
                )
            )
        ).scalar_one()
        assert float(entry.cost_usd) == pytest.approx(0.19)
        assert float(entry.margin) == pytest.approx(3.0)
        assert entry.ref_table == "media_calls"
        assert entry.ref_ids == [11, 12, 13]


async def test_zero_hold_is_allowed_for_free_steps() -> None:
    """Локальные шаги (сборка, публикация) стоят ноль и не должны падать."""
    tenant = _tenant()
    async with session_scope() as s:
        await cl.topup(s, tenant, 1_000)
        hold = await cl.open_hold(s, tenant, project_id=1, step_code="assemble", amount_micro=0)
        await cl.settle_hold(s, hold.id, cost_usd=0.0)
        assert await _assert_consistent(s, tenant) == 1_000
