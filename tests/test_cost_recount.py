"""Пересчёт фактов и списание Create-задачи по факту.

Проверяется главный контракт денег: списываем сумму фактических вызовов
(`media_calls.cost_usd`) по их id, а не оценку каталога; повторный пересчёт
ничего не дублирует; модель без подтверждённой цены остаётся `unpriced`, а не
превращается в ноль и не «списывается» выдуманной цифрой.
"""

from __future__ import annotations

import json
import uuid

import pytest

from app.db import session_scope
from app.models import CreditEntry, LlmCall, MediaCall
from app.services import credit_ledger as cl
from app.services import llm_ledger, media_ledger
from app.services.cost_recount import facts_for_rows, recount_costs
from app.services.create_billing import charge_hold_by_fact
from app.services.credits import price_micro


@pytest.fixture(autouse=True)
def _reset_ledgers() -> None:
    media_ledger._warned_models.clear()
    media_ledger._pending_rows.clear()
    llm_ledger._pending_rows.clear()
    llm_ledger._prices.cache_clear()


def _write_prices(path, models: dict) -> None:
    path.write_text(json.dumps({"models": models}), encoding="utf-8")


def _tenant() -> str:
    return str(uuid.uuid4())


async def _media_rows() -> list[MediaCall]:
    from sqlalchemy import select

    async with session_scope() as s:
        return list((await s.execute(select(MediaCall))).scalars().all())


# ── пересчёт media_calls ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_recount_dry_run_does_not_write_then_apply_does(tmp_path, monkeypatch) -> None:
    """Примерка показывает прирост, запись — пишет; повтор даёт ноль."""
    prices = tmp_path / "prices.json"
    _write_prices(prices, {})
    monkeypatch.setattr(media_ledger, "_PRICES_PATH", prices)

    await media_ledger.record(provider="kie", kind="image", model="flux-2-pro", units=1.0, unit="item")

    # Прайс закрыли задним числом — историческая строка должна пересчитаться.
    _write_prices(prices, {"kie:flux-2-pro": {"unit": "item", "usd_per_unit": 0.05}})

    dry = await recount_costs(dry_run=True)
    assert dry["dry_run"] is True
    assert dry["scanned"] == 1
    assert dry["priced"] == 1
    assert dry["added_usd"] == pytest.approx(0.05)
    rows = await _media_rows()
    assert rows[0].cost_usd == 0.0
    assert rows[0].unpriced is True

    applied = await recount_costs(dry_run=False)
    assert applied["priced"] == 1
    assert applied["added_usd"] == pytest.approx(0.05)
    rows = await _media_rows()
    assert rows[0].cost_usd == pytest.approx(0.05)
    assert rows[0].unpriced is False

    second = await recount_costs(dry_run=False)
    assert second["priced"] == 0
    assert second["added_usd"] == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_recount_keeps_unknown_model_unpriced(tmp_path, monkeypatch) -> None:
    """Нет цены — строка остаётся unpriced: ноль не подставляется."""
    prices = tmp_path / "prices.json"
    _write_prices(prices, {})
    monkeypatch.setattr(media_ledger, "_PRICES_PATH", prices)

    await media_ledger.record(provider="outsee", kind="image", model="нет-такой", units=1.0, unit="item")

    result = await recount_costs(dry_run=False)

    assert result["priced"] == 0
    assert result["still_unpriced"] == 1
    assert result["added_usd"] == pytest.approx(0.0)
    rows = await _media_rows()
    assert rows[0].cost_usd == 0.0
    assert rows[0].unpriced is True


@pytest.mark.asyncio
async def test_recount_modes_unpriced_vs_all(tmp_path, monkeypatch) -> None:
    """Кнопка админки не переписывает оценённое; полный пересчёт — только явно."""
    prices = tmp_path / "prices.json"
    _write_prices(prices, {})
    monkeypatch.setattr(media_ledger, "_PRICES_PATH", prices)
    await media_ledger.record(provider="kie", kind="image", model="flux-2-pro", units=1.0, unit="item")
    _write_prices(prices, {"kie:flux-2-pro": {"unit": "item", "usd_per_unit": 0.05}})

    by_default = await recount_costs(dry_run=False)
    assert by_default["mode"] == "unpriced"
    assert by_default["priced"] == 1

    # Цена выросла, но строка уже оценена: режим unpriced её не трогает.
    _write_prices(prices, {"kie:flux-2-pro": {"unit": "item", "usd_per_unit": 0.08}})
    unpriced_again = await recount_costs(dry_run=False)
    assert unpriced_again["scanned"] == 0
    assert unpriced_again["priced"] == 0
    rows = await _media_rows()
    assert rows[0].cost_usd == pytest.approx(0.05)

    # Явный полный пересчёт — дельта к прежней цене.
    full = await recount_costs(dry_run=False, mode="all")
    assert full["mode"] == "all"
    assert full["scanned"] == 1
    assert full["priced"] == 1
    assert full["added_usd"] == pytest.approx(0.03)
    rows = await _media_rows()
    assert rows[0].cost_usd == pytest.approx(0.08)


@pytest.mark.asyncio
async def test_recount_rejects_unknown_mode() -> None:
    with pytest.raises(ValueError):
        await recount_costs(mode="быстро")


# ── пересчёт llm_calls ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_recount_llm_cost_from_tokens(tmp_path, monkeypatch) -> None:
    """Цена LLM пересчитывается из токенов, а не берётся из ниоткуда."""
    prices = tmp_path / "llm.json"
    _write_prices(prices, {})
    monkeypatch.setattr(llm_ledger, "_PRICES_PATH", prices)
    llm_ledger._prices.cache_clear()

    async with session_scope() as s:
        s.add(
            LlmCall(
                tenant_id=None,
                project_id=None,
                node_key="chat",
                logical_call_id="lc-test",
                model="gpt-5-6-sol",
                prompt_tokens=1000,
                completion_tokens=1000,
                total_tokens=2000,
                cost_usd=0.0,
            )
        )

    _write_prices(prices, {"gpt-5-6-sol": {"input_usd_per_m": 1.0, "output_usd_per_m": 2.0}})
    llm_ledger._prices.cache_clear()

    result = await recount_costs(dry_run=False)
    assert result["priced"] == 1
    # 1000 * $1/1M + 1000 * $2/1M = $0.003
    assert result["added_usd"] == pytest.approx(0.003)

    async with session_scope() as s:
        from sqlalchemy import select

        row = (await s.execute(select(LlmCall))).scalars().one()
        assert row.cost_usd == pytest.approx(0.003)


@pytest.mark.asyncio
async def test_recount_prices_null_llm_row(tmp_path, monkeypatch) -> None:
    """Строка с ценой NULL — кандидат кнопки; после прайса получает цену."""
    prices = tmp_path / "llm.json"
    _write_prices(prices, {})
    monkeypatch.setattr(llm_ledger, "_PRICES_PATH", prices)
    llm_ledger._prices.cache_clear()

    async with session_scope() as s:
        s.add(
            LlmCall(
                project_id=None,
                node_key="adhoc",
                logical_call_id="lc-null",
                model="новая-модель-llm",
                prompt_tokens=1000,
                completion_tokens=0,
                cost_usd=None,
            )
        )

    _write_prices(prices, {"новая-модель-llm": {"input_usd_per_m": 2.0, "output_usd_per_m": 2.0}})
    llm_ledger._prices.cache_clear()

    result = await recount_costs(dry_run=False)
    assert result["scanned"] == 1
    assert result["priced"] == 1
    assert result["added_usd"] == pytest.approx(0.002)

    async with session_scope() as s:
        from sqlalchemy import select

        row = (await s.execute(select(LlmCall))).scalars().one()
        assert row.cost_usd == pytest.approx(0.002)


# ── списание Create-задачи по факту ───────────────────────────────────────


async def _hold(tenant: str, est_usd: float) -> str:
    async with session_scope() as s:
        await cl.topup(s, tenant, 10 * 10**6, memo="тест")
        hold = await cl.open_hold(
            s, tenant, project_id=0, step_code="kie_image", amount_micro=price_micro(est_usd)
        )
        return hold.id


@pytest.mark.asyncio
async def test_charge_hold_by_fact_uses_actual_rows(tmp_path, monkeypatch) -> None:
    """Списываем сумму фактов по их id, а не оценку; повтор не дублирует."""
    prices = tmp_path / "prices.json"
    _write_prices(prices, {"kie:flux-2-pro": {"unit": "item", "usd_per_unit": 0.05}})
    monkeypatch.setattr(media_ledger, "_PRICES_PATH", prices)

    tenant = _tenant()
    hold_id = await _hold(tenant, est_usd=1.0)

    with media_ledger.capture_rows() as rows:
        await media_ledger.record(provider="kie", kind="image", model="flux-2-pro", units=1.0, unit="item")
        await media_ledger.record(provider="kie", kind="image", model="flux-2-pro", units=1.0, unit="item")
    assert len(rows) == 2

    async with session_scope() as s:
        charge = await charge_hold_by_fact(s, hold_id, rows, est_usd=1.0, memo="KIE генерация image")
    assert charge.calls == 2
    assert charge.charged_usd == pytest.approx(0.10)
    assert charge.ref_ids == rows

    # Повторный расчёт того же холда — не ошибка и не второе списание.
    async with session_scope() as s:
        await charge_hold_by_fact(s, hold_id, rows, est_usd=1.0, memo="KIE генерация image")

    async with session_scope() as s:
        from sqlalchemy import select

        entries = (
            (await s.execute(select(CreditEntry).where(CreditEntry.hold_id == hold_id))).scalars().all()
        )
        settles = [e for e in entries if e.kind == "settle"]
        assert len(settles) == 1
        assert settles[0].ref_ids == rows
        assert settles[0].ref_table == "media_calls"
        assert int(settles[0].delta_micro) == -price_micro(0.10)


@pytest.mark.asyncio
async def test_unpriced_fact_is_not_charged(tmp_path, monkeypatch) -> None:
    """Нет цены — не списываем выдуманную цифру, но факт и memo остаются."""
    prices = tmp_path / "prices.json"
    _write_prices(prices, {})
    monkeypatch.setattr(media_ledger, "_PRICES_PATH", prices)

    tenant = _tenant()
    hold_id = await _hold(tenant, est_usd=1.0)

    with media_ledger.capture_rows() as rows:
        await media_ledger.record(provider="kie", kind="image", model="новая-модель", units=1.0, unit="item")

    async with session_scope() as s:
        charge = await charge_hold_by_fact(s, hold_id, rows, est_usd=1.0, memo="KIE генерация image")
    assert charge.charged_usd == 0.0
    assert charge.unpriced_calls == 1
    assert "unpriced" in charge.memo and "оценка" in charge.memo

    async with session_scope() as s:
        got = await facts_for_rows(s, rows)
        assert got.calls == 1 and got.unpriced_calls == 1 and got.cost_usd == 0.0


@pytest.mark.asyncio
async def test_facts_for_rows_ignores_missing_ids() -> None:
    """Чужие/удалённые id не превращаются в фиктивные вызовы."""
    async with session_scope() as s:
        got = await facts_for_rows(s, [])
        assert got.calls == 0 and got.cost_usd == 0.0
