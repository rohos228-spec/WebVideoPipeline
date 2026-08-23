"""Деньги: перевод себестоимости в кредиты.

Проверяется не «функция вернула число», а свойства, на которых держится
касса: цена никогда не ниже себестоимости с маржой, баланс никогда не выше
фактического, дешёвая операция выглядит дешёвой.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.services.credits import (
    MICRO,
    format_credits,
    hold_micro,
    margin,
    micro_to_usd,
    price_micro,
)


def test_price_matches_owner_decision() -> None:
    """Курс 1 кредит = $1, маржа ×3 — решение владельца (§5.1)."""
    assert margin() == Decimal("3")
    # Ролик целиком: $5.625 себестоимости → 16.88 кредита в прайсе §6.2.
    assert format_credits(price_micro(5.625), rounding="up") == "16.88"
    # 24 клипа 768P — самая крупная строка сметы, цена из §7.3.
    assert format_credits(price_micro(0.19 * 24), rounding="up") == "13.68"


def test_price_never_below_cost_with_margin() -> None:
    """Округление цены вверх — гарантия, а не деталь реализации."""
    for cost in (0.0035, 0.006, 0.0105, 0.19, 1.0, 5.625):
        micro = price_micro(cost)
        assert Decimal(micro) / MICRO >= Decimal(str(cost)) * margin()


def test_cheap_operation_looks_cheap() -> None:
    """Картинка стоит 0.0105 кредита и должна так и выглядеть.

    Округление до двух знаков вверх показало бы 0.02 — почти вдвое дороже.
    На дешевизне картинки держится всё поведение продукта: итерация идёт по
    кадрам, видео покупается один раз (§6.3).
    """
    assert format_credits(price_micro(0.0035), rounding="up") == "0.0105"


def test_balance_rounds_down_price_rounds_up() -> None:
    micro = 1_234_567  # 1.234567 кредита
    assert format_credits(micro, rounding="down") == "1.23"
    assert format_credits(micro, rounding="up") == "1.24"


def test_dust_is_visible_not_zero() -> None:
    """Ненулевой остаток не показывается нулём: рядом с кнопкой «списать»
    «0.00» — это ложь."""
    assert format_credits(1, rounding="down") == "<0.0001"
    assert format_credits(0) == "0"


def test_no_free_work_on_fragmentation() -> None:
    """24 картинки по отдельности стоят не меньше, чем пачкой.

    Если бы цена округлялась к ближайшему, дробление шага на кадры делало
    бы работу бесплатной.
    """
    one = price_micro(0.0035)
    assert one * 24 >= price_micro(0.0035 * 24)


def test_hold_is_p90_not_median() -> None:
    assert hold_micro(1.0) == price_micro(1.0)
    assert hold_micro(2.0) > hold_micro(1.0)


def test_micro_to_usd_roundtrip() -> None:
    usd = Decimal("5.625")
    assert micro_to_usd(price_micro(usd)) == pytest.approx(usd, abs=Decimal("0.000001"))


def test_storage_is_integer() -> None:
    """Микрокредиты — целые: в деньгах float не бывает."""
    assert isinstance(price_micro(0.0035), int)
    assert isinstance(hold_micro(0.0035), int)
