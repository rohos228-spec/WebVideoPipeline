"""Этап 3 (блок C): каркас учёта llm_calls — цены, запись, best effort."""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base, LlmCall
from app.services import llm_ledger as ledger


@pytest_asyncio.fixture
async def ledger_db(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'ledger.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    @asynccontextmanager
    async def scope():
        async with factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    monkeypatch.setattr(ledger, "session_scope", scope)
    yield factory
    await engine.dispose()


@pytest.fixture(autouse=True)
def _reset_counters(monkeypatch):
    monkeypatch.setattr(ledger, "_failed_inserts", 0)
    ledger._unpersisted_spent.clear()
    ledger._warned_models.clear()


# ── прайс ─────────────────────────────────────────────────────────────────


def test_price_known_model():
    in_rate, out_rate = ledger.price_for("gpt-5.6-sol")
    assert in_rate > 0 and out_rate > in_rate


def test_price_normalizes_provider_prefix():
    assert ledger.price_for("openai/gpt-5.6-sol") == ledger.price_for("gpt-5.6-sol")


def test_price_unknown_model_zero_and_warns_once():
    # caplog не видит loguru (нет моста в conftest) — проверяем механизм
    # «warning один раз на модель» по warn-набору напрямую.
    assert ledger.price_for("no-such-model-x") == (0.0, 0.0)
    assert "no-such-model-x" in ledger._warned_models
    before = set(ledger._warned_models)
    ledger.price_for("no-such-model-x")
    assert ledger._warned_models == before  # без дубля


def test_price_served_model_priority():
    # запрошенная неизвестна, served известна → цена по served
    assert ledger.price_for("unknown-req", "gpt-5.6-sol") == ledger.price_for(
        "gpt-5.6-sol"
    )


# ── compute_cost / unbilled ──────────────────────────────────────────────


def test_cost_chat_usage_keys():
    cost, pt, ct, tt, unbilled = ledger.compute_cost(
        {"prompt_tokens": 1_000_000, "completion_tokens": 1_000_000},
        model="gpt-5.6-sol",
    )
    in_rate, out_rate = ledger.price_for("gpt-5.6-sol")
    assert cost == pytest.approx(in_rate + out_rate)
    assert (pt, ct, tt) == (1_000_000, 1_000_000, None)
    assert not unbilled


def test_cost_responses_usage_keys():
    cost, pt, ct, tt, unbilled = ledger.compute_cost(
        {"input_tokens": 500, "output_tokens": 100, "total_tokens": 600},
        model="gpt-5.6-sol",
    )
    assert (pt, ct, tt) == (500, 100, 600)
    assert cost > 0 and not unbilled


def test_unbilled_only_when_no_token_fields():
    cost, pt, ct, tt, unbilled = ledger.compute_cost({}, model="gpt-5.6-sol")
    assert unbilled and cost == 0.0 and (pt, ct, tt) == (None, None, None)
    # Этап 3 [панель 1/3]: частичный usage — НЕ unbilled.
    _, pt, ct, _, unbilled = ledger.compute_cost(
        {"prompt_tokens": 10}, model="gpt-5.6-sol"
    )
    assert not unbilled and pt == 10 and ct is None


# ── record ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_record_writes_row(ledger_db):
    row_id = await ledger.record(
        project_id=7,
        node_key="check_1",
        logical_call_id="lc-1",
        model="gpt-5.6-sol",
        usage={"prompt_tokens": 100, "completion_tokens": 10},
        relay="chattiq.ru",
        endpoint="chat",
        result="ok",
    )
    assert row_id is not None
    async with ledger_db() as s:
        row = (await s.execute(select(LlmCall))).scalar_one()
    assert row.project_id == 7 and row.node_key == "check_1"
    assert row.cost_usd > 0 and not row.unbilled and row.result == "ok"


@pytest.mark.asyncio
async def test_record_failed_call_unbilled(ledger_db):
    await ledger.record(
        project_id=7,
        node_key="check_1",
        logical_call_id="lc-2",
        model="gpt-5.6-sol",
        usage=None,
        result="error",
        error_kind="timeout",
    )
    async with ledger_db() as s:
        row = (await s.execute(select(LlmCall))).scalar_one()
    assert row.result == "error" and row.error_kind == "timeout"
    assert row.unbilled and row.prompt_tokens is None and row.cost_usd == 0.0


@pytest.mark.asyncio
async def test_record_best_effort_on_db_failure(monkeypatch):
    @asynccontextmanager
    async def broken_scope():
        raise RuntimeError("database is locked")
        yield  # pragma: no cover

    monkeypatch.setattr(ledger, "session_scope", broken_scope)
    row_id = await ledger.record(
        project_id=3,
        node_key="n",
        logical_call_id="lc-3",
        model="gpt-5.6-sol",
        usage={"prompt_tokens": 1_000_000},
    )
    assert row_id is None  # вызов не упал
    assert ledger.failed_insert_count() == 1
    assert ledger.unpersisted_spent(3) > 0.0
    assert ledger.unpersisted_spent(99) == 0.0
