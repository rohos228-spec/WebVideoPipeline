"""Учёт медиа-генераций (media_calls) — п.24 плана техдолга.

До этого «стоимость ролика» в UI считала только текстовые LLM, хотя для
видеоконвейера основная статья — картинки, видео и озвучка.

Ключевое отличие от `llm_ledger`: у медиа-провайдеров нет `usage`, платят
за единицы. Поэтому `units` пишутся всегда, а `cost_usd` — только если
модель есть в прайсе; иначе строка помечается `unpriced`.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base, MediaCall
from app.services import media_ledger


@pytest_asyncio.fixture
async def session(tmp_path, monkeypatch):
    """Своя база на тест — record() ходит через session_scope модуля."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'media.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    @asynccontextmanager
    async def scope():
        async with factory() as s:
            try:
                yield s
                await s.commit()
            except Exception:
                await s.rollback()
                raise

    monkeypatch.setattr(media_ledger, "session_scope", scope)
    async with factory() as reader:
        yield reader
    await engine.dispose()


@pytest.fixture(autouse=True)
def _reset_price_cache(monkeypatch) -> None:
    monkeypatch.setattr(media_ledger, "_failed_inserts", 0)
    media_ledger._warned_models.clear()


async def _rows(session) -> list[MediaCall]:
    return list((await session.execute(select(MediaCall))).scalars().all())


@pytest.mark.asyncio
async def test_unknown_model_is_recorded_but_unpriced(session) -> None:
    """Модели нет в прайсе → единицы посчитаны, цена 0, флаг unpriced."""
    await media_ledger.record(provider="outsee", kind="image", model="какая-то-новая", units=1.0)

    rows = await _rows(session)
    assert len(rows) == 1
    assert rows[0].units == 1.0
    assert rows[0].cost_usd == 0.0
    assert rows[0].unpriced is True


@pytest.mark.asyncio
async def test_priced_model_costs_units_times_rate(session, monkeypatch, tmp_path) -> None:
    """Цена задана → cost = units × usd_per_unit."""
    prices = tmp_path / "media_prices.json"
    prices.write_text(
        json.dumps({"models": {"outsee:veo-3-1-lite": {"unit": "second", "usd_per_unit": 0.25}}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(media_ledger, "_PRICES_PATH", prices)

    await media_ledger.record(provider="outsee", kind="video", model="veo-3-1-lite", units=8.0)

    rows = await _rows(session)
    assert len(rows) == 1
    assert rows[0].cost_usd == pytest.approx(2.0)
    assert rows[0].unit == "second"
    assert rows[0].unpriced is False


@pytest.mark.asyncio
async def test_null_price_means_unpriced(session, monkeypatch, tmp_path) -> None:
    """usd_per_unit: null — «цена неизвестна», а не «бесплатно»."""
    prices = tmp_path / "media_prices.json"
    prices.write_text(
        json.dumps({"models": {"grsai:sora-2": {"unit": "second", "usd_per_unit": None}}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(media_ledger, "_PRICES_PATH", prices)

    await media_ledger.record(provider="grsai", kind="video", model="sora-2", units=10.0)

    rows = await _rows(session)
    assert rows[0].unpriced is True
    assert rows[0].units == 10.0
    assert rows[0].unit == "second"


@pytest.mark.asyncio
async def test_media_call_records_failure_and_reraises(session) -> None:
    """Неуспешная генерация тоже попадает в учёт — за неё платят."""

    class Boom(RuntimeError):
        pass

    with pytest.raises(Boom):
        async with media_ledger.media_call("outsee", "image", model="gpt-image-2", units=1.0):
            raise Boom("провайдер отверг")

    rows = await _rows(session)
    assert len(rows) == 1
    assert rows[0].result == "error"
    assert rows[0].error_kind == "Boom"


@pytest.mark.asyncio
async def test_media_call_fills_fields_inside_block(session) -> None:
    """units/external_id известны только после ответа API — дозаполняются внутри."""
    async with media_ledger.media_call("outsee", "video", model="veo-3-1-lite") as call:
        call.units = 6.0
        call.unit = "second"
        call.external_id = "gen-42"

    rows = await _rows(session)
    assert rows[0].units == 6.0
    assert rows[0].external_id == "gen-42"
    assert rows[0].result == "ok"


@pytest.mark.asyncio
async def test_accounting_context_supplies_project_and_node(session) -> None:
    """project/node берутся из того же contextvar, что у текстового учёта."""
    from app.services.llm_override import LlmAccountingContext, use_accounting

    with use_accounting(LlmAccountingContext(project_id=7, node_key="n_img")):
        await media_ledger.record(provider="grsai", kind="image", model="gpt-image-2", units=1.0)

    rows = await _rows(session)
    assert rows[0].project_id == 7
    assert rows[0].node_key == "n_img"


@pytest.mark.asyncio
async def test_totals_groups_and_counts_unpriced(session) -> None:
    for _ in range(3):
        await media_ledger.record(
            provider="outsee", kind="image", model="нет-в-прайсе", units=1.0, project_id=5
        )
    await media_ledger.record(provider="outsee", kind="video", model="нет-в-прайсе", units=8.0, project_id=5)

    got = await media_ledger.totals(5)

    assert got["unpriced_calls"] == 4
    assert got["cost_usd"] == 0.0
    kinds = {row["kind"]: row for row in got["by_provider"]}
    assert kinds["image"]["calls"] == 3
    assert kinds["video"]["units"] == 8.0


@pytest.mark.asyncio
async def test_shipped_price_table_is_parseable() -> None:
    """Файл прайса в репо читается и все записи имеют unit."""
    prices = media_ledger._prices()
    assert prices, "media_prices.json не прочитан"
    for key, (unit, value) in prices.items():
        assert unit, key
        assert value is None or value >= 0.0, key
