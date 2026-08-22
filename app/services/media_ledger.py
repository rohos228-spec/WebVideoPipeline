"""Учёт медиа-генераций (картинки / видео / озвучка) — таблица ``media_calls``.

Зачем. `llm_ledger` считает только текстовые вызовы, а для видеоконвейера
основная статья расходов — картинки, видео и TTS. «Стоимость ролика» в UI
до этого показывала текст и молчала про остальное (`docs/TECH_DEBT_PLAN.md`
п.24).

Отличие от текстового учёта: у медиа-провайдеров нет `usage`. Платят за
единицы — кадр, секунду видео, символ текста. Поэтому:

* ``units`` + ``unit`` пишутся ВСЕГДА, включая неуспешные вызовы;
* ``cost_usd`` считается, только если модель есть в `media_prices.json` с
  ненулевой ценой; иначе строка помечается ``unpriced=True``.

Так дашборд честно показывает «N генераций без цены» вместо тихого нуля:
реальные тарифы outsee/grsai/ElevenLabs зависят от плана владельца, и
выдумывать их в коде нельзя.

Учёт никогда не валит платный вызов: сбой INSERT — WARNING и счётчик.

Использование — контекстный менеджер вокруг фактической генерации::

    async with media_call("outsee", "video", model=slug) as call:
        result = await _do_generate(...)
        call.units = duration_sec
        call.external_id = str(gen_id)
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger

from app.db import session_scope
from app.models import MediaCall

_PRICES_PATH = Path(__file__).resolve().parent / "media_prices.json"

_failed_inserts = 0
_warned_models: set[str] = set()


@dataclass(frozen=True)
class PriceEntry:
    """Строка прайса: либо ставка за единицу, либо цена за генерацию.

    ``per_unit`` — цена за единицу (кадр, секунда, символ).
    ``per_call`` — цена за генерацию целиком (так тарифицируется видео).
    Оба ``None`` — цена неизвестна: единицы считаем, деньги нет.
    """

    unit: str
    per_unit: float | None
    per_call: float | None


@dataclass
class MediaCallInfo:
    """Мутируемый «черновик» строки — заполняется по ходу генерации."""

    provider: str
    kind: str
    model: str = ""
    units: float = 0.0
    unit: str = ""
    # Уточнение тарифа внутри модели, напр. "1080P:6" — см. price_for().
    variant: str = ""
    external_id: str = ""
    extra: dict[str, object] = field(default_factory=dict)


def _price_entry(spec: dict[str, Any], key: str) -> PriceEntry:
    unit = str(spec.get("unit") or "item")

    def _num(field: str) -> float | None:
        raw = spec.get(field)
        if raw is None:
            return None
        try:
            return float(raw)
        except (TypeError, ValueError):
            logger.warning("media_ledger: битая цена {} для {}", field, key)
            return None

    return PriceEntry(unit=unit, per_unit=_num("usd_per_unit"), per_call=_num("usd_per_call"))


def _prices() -> dict[str, PriceEntry]:
    """{'provider:model' или 'provider:model@variant' → PriceEntry}."""
    try:
        doc = json.loads(_PRICES_PATH.read_text(encoding="utf-8"))
        models = doc.get("models") or {}
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("media_ledger: прайс не прочитан ({}) — все цены неизвестны", e)
        return {}
    out: dict[str, PriceEntry] = {}
    for key, spec in models.items():
        if not isinstance(spec, dict):
            logger.warning("media_ledger: битая строка прайса для {}", key)
            continue
        out[key] = _price_entry(spec, key)
    return out


def price_for(provider: str, model: str, variant: str = "") -> PriceEntry:
    """Цена модели. `variant` уточняет тариф внутри модели.

    У видео-провайдеров ставка зависит не только от модели: MiniMax берёт
    за КЛИП, и цена меняется с разрешением и длительностью (768P/6s — одна,
    1080P/6s — другая). Поэтому ключ прайса может быть уточнённым:
    ``minimax:MiniMax-Hailuo-2.3@1080P:6``. Если такого ключа нет, падаем на
    общий ``provider:model`` — так провайдеры с плоским тарифом (картинки,
    символы TTS) не требуют вариантов вовсе.
    """
    prov = (provider or "").strip().lower()
    base_key = f"{prov}:{(model or '').strip()}"
    keys = [f"{base_key}@{variant.strip()}"] if variant.strip() else []
    keys.append(base_key)

    prices = _prices()
    for key in keys:
        if key in prices:
            return prices[key]

    warn_key = keys[0]
    if warn_key not in _warned_models:
        _warned_models.add(warn_key)
        logger.warning(
            "media_ledger: {} нет в media_prices.json — единицы посчитаны, цена неизвестна",
            warn_key,
        )
    return PriceEntry(unit="item", per_unit=None, per_call=None)


def compute_cost(
    provider: str,
    model: str,
    units: float,
    unit_hint: str = "",
    variant: str = "",
) -> tuple[float, str, bool]:
    """(cost_usd, unit, unpriced).

    ``usd_per_call`` важнее ``usd_per_unit``: если провайдер берёт за
    генерацию целиком, умножать на секунды нельзя — 10-секундный клип у
    MiniMax стоит не вдвое дороже шестисекундного.
    """
    entry = price_for(provider, model, variant)
    unit = unit_hint or entry.unit
    if entry.per_call is not None:
        return (round(entry.per_call, 6), unit, False)
    if entry.per_unit is None:
        return (0.0, unit, True)
    return (round(max(0.0, float(units)) * entry.per_unit, 6), unit, False)


def _accounting() -> tuple[int | None, str]:
    """project_id / node_key из того же contextvar, что у текстового учёта."""
    try:
        from app.services.llm_override import current_accounting

        ctx = current_accounting()
    except Exception:  # noqa: BLE001 — учёт не должен валить генерацию
        ctx = None
    if ctx is None:
        return (None, "adhoc")
    return (ctx.project_id, ctx.node_key or "adhoc")


async def record(
    *,
    provider: str,
    kind: str,
    model: str = "",
    units: float = 0.0,
    unit: str = "",
    result: str = "ok",
    error_kind: str = "",
    external_id: str = "",
    duration_ms: int = 0,
    project_id: int | None = None,
    node_key: str = "",
    variant: str = "",
) -> int | None:
    """Записать одну медиа-генерацию. Best effort: сбой INSERT не валит вызов."""
    global _failed_inserts

    ctx_project, ctx_node = _accounting()
    pid = project_id if project_id is not None else ctx_project
    node = node_key or ctx_node

    cost, resolved_unit, unpriced = compute_cost(provider, model, units, unit, variant)
    try:
        async with session_scope() as session:
            row = MediaCall(
                project_id=pid,
                node_key=node or "adhoc",
                provider=(provider or "")[:40],
                kind=(kind or "")[:20],
                model=(model or "")[:120],
                units=float(units),
                unit=resolved_unit[:20],
                cost_usd=cost,
                result=result,
                error_kind=(error_kind or "")[:60],
                unpriced=unpriced,
                external_id=(external_id or "")[:120],
                duration_ms=int(duration_ms),
            )
            session.add(row)
            await session.flush()
            return row.id
    except Exception as e:  # noqa: BLE001 — учёт не валит платную генерацию
        _failed_inserts += 1
        logger.warning("media_ledger: INSERT media_calls упал (#{} всего): {}", _failed_inserts, e)
        return None


def failed_insert_count() -> int:
    """Ненулевой = данные учёта неполные (отдаётся в API дашборда)."""
    return _failed_inserts


@asynccontextmanager
async def media_call(
    provider: str,
    kind: str,
    *,
    model: str = "",
    units: float = 0.0,
    unit: str = "",
    variant: str = "",
    project_id: int | None = None,
) -> AsyncIterator[MediaCallInfo]:
    """Обернуть генерацию: замерить время, записать ok/error.

    Поля ``units`` / ``model`` / ``external_id`` дозаполняются внутри блока —
    длительность видео и id генерации известны только после ответа API.
    Исключение пробрасывается: учёт фиксирует строку с ``result="error"`` и
    отдаёт ошибку дальше.
    """
    info = MediaCallInfo(
        provider=provider, kind=kind, model=model, units=units, unit=unit, variant=variant
    )
    started = time.monotonic()
    try:
        yield info
    except BaseException as exc:
        await record(
            provider=info.provider,
            kind=info.kind,
            model=info.model,
            units=info.units,
            unit=info.unit,
            result="error",
            error_kind=type(exc).__name__,
            external_id=info.external_id,
            duration_ms=int((time.monotonic() - started) * 1000),
            project_id=project_id,
            variant=info.variant,
        )
        raise
    await record(
        provider=info.provider,
        kind=info.kind,
        model=info.model,
        units=info.units,
        unit=info.unit,
        result="ok",
        external_id=info.external_id,
        duration_ms=int((time.monotonic() - started) * 1000),
        project_id=project_id,
        variant=info.variant,
    )


async def totals(project_id: int, session: Any = None) -> dict[str, object]:
    """Сводка по проекту: деньги, единицы, сколько строк без цены.

    `session` передаёт вызывающий (роутер), если у него уже есть своя —
    иначе открывается новая через `session_scope`. Без этого дашборд
    читал ДРУГУЮ базу, чем та, в которую писал тест или запрос: у
    `session_scope` свой движок по `settings.sqlite_path`.
    """
    from sqlalchemy import case, func, select

    # SUM по Boolean-колонке нельзя: SQLAlchemy прогоняет результат через
    # result processor типа и возвращает True/False вместо количества —
    # три unpriced-строки в группе давали 1. Считаем через CASE, как в
    # `web/routers/llm_costs.py`.
    unpriced_n = func.sum(case((MediaCall.unpriced.is_(True), 1), else_=0))

    stmt = (
        select(
            MediaCall.provider,
            MediaCall.kind,
            func.sum(MediaCall.cost_usd),
            func.sum(MediaCall.units),
            func.count(),
            unpriced_n,
        )
        .where(MediaCall.project_id == project_id)
        .group_by(MediaCall.provider, MediaCall.kind)
    )

    if session is not None:
        rows = (await session.execute(stmt)).all()
    else:
        async with session_scope() as own:
            rows = (await own.execute(stmt)).all()

    by_provider: list[dict[str, object]] = []
    total_cost = 0.0
    unpriced_calls = 0
    for provider, kind, cost, units, count, unpriced in rows:
        cost_f = float(cost or 0.0)
        total_cost += cost_f
        unpriced_calls += int(unpriced or 0)
        by_provider.append(
            {
                "provider": provider,
                "kind": kind,
                "cost_usd": round(cost_f, 6),
                "units": float(units or 0.0),
                "calls": int(count or 0),
                "unpriced_calls": int(unpriced or 0),
            }
        )
    return {
        "cost_usd": round(total_cost, 6),
        "unpriced_calls": unpriced_calls,
        "failed_inserts": failed_insert_count(),
        "by_provider": by_provider,
    }
