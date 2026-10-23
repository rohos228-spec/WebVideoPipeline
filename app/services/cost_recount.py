"""Единый пересчёт фактов расходов из прайсов — для CLI и админ-ручки.

Зачем отдельный модуль. Пересчёт нужен в двух местах: разовый CLI
(`app/backfill_media_costs.py`) и ручка `POST /api/admin/costs/recount`.
Пока логика живёт в скрипте, ручка неизбежно обрастает копией, и цены в
отчёте начинают зависеть от того, кто пересчитывал. Здесь одна функция
`recount_costs` — её зовут оба.

Что пересчитывается:

* `media_calls` — цена берётся из `media_prices.json`. Строка, у которой
  цены по-прежнему нет, остаётся `unpriced` (врать нулём нельзя);
* `llm_calls` — цена пересчитывается из токенов и `llm_prices.json`;
  без `usage` (`unbilled`) строка не расценивается.

`added_usd` — это прирост суммы `cost_usd` по окну, а не сумма всех цен:
так повторный пересчёт после заполнения прайса даёт ровно ту разницу,
которая доехала до отчёта.

Повторный запуск идемпотентен: уже расценённые строки с той же ценой не
трогаются, а `dry_run` ничего не пишет.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from loguru import logger
from sqlalchemy import select

from app.db import session_scope
from app.models import LlmCall, MediaCall
from app.services.llm_ledger import compute_cost as llm_compute_cost
from app.services.llm_ledger import price_for as llm_price_for
from app.services.media_ledger import compute_cost as media_compute_cost

#: Порог, ниже которого разницу цен считаем нулевой (float-шум).
_EPS = 1e-9


@dataclass
class FactTotals:
    """Итог по конкретным строкам журналов — для списания по факту."""

    calls: int = 0
    priced_calls: int = 0
    unpriced_calls: int = 0
    cost_usd: float = 0.0
    ref_ids: list[int] = field(default_factory=list)


@dataclass
class RecountResult:
    """Результат пересчёта — ровно контракт `POST /api/admin/costs/recount`."""

    scanned: int = 0
    priced: int = 0
    still_unpriced: int = 0
    added_usd: float = 0.0
    dry_run: bool = True
    mode: str = "unpriced"

    def to_dict(self) -> dict[str, Any]:
        return {
            "scanned": self.scanned,
            "priced": self.priced,
            "still_unpriced": self.still_unpriced,
            "added_usd": round(self.added_usd, 6),
            "dry_run": self.dry_run,
            "mode": self.mode,
        }


async def facts_for_rows(session: Any, row_ids: list[int]) -> FactTotals:
    """Сумма фактов по id строк `media_calls` и `llm_calls`.

    Этим касса списывает ровно свои вызовы, а не оценку каталога: id собраны
    `media_ledger.capture_rows` / `llm_ledger.capture_rows` вокруг работы.
    Неизвестная цена не превращается в ноль — она попадает в
    `unpriced_calls`, и вызывающий решает, что писать в memo. `NULL` в
    `llm_calls` — тот же случай «цены не знаем»: в сумму идёт 0, но вызов
    честно считается нерасцененным.
    """
    ids = [int(i) for i in row_ids if i]
    if not ids:
        return FactTotals()
    totals = FactTotals()
    media_rows = (
        await session.execute(
            select(MediaCall.id, MediaCall.cost_usd, MediaCall.unpriced).where(
                MediaCall.id.in_(ids),
                MediaCall.result == "ok",
            )
        )
    ).all()
    for row_id, cost, unpriced in media_rows:
        totals.calls += 1
        totals.ref_ids.append(int(row_id))
        totals.cost_usd += float(cost or 0.0)
        if unpriced:
            totals.unpriced_calls += 1
        else:
            totals.priced_calls += 1
    llm_rows = (
        await session.execute(
            select(LlmCall.id, LlmCall.cost_usd).where(
                LlmCall.id.in_(ids),
                LlmCall.result == "ok",
            )
        )
    ).all()
    for row_id, cost in llm_rows:
        totals.calls += 1
        totals.ref_ids.append(int(row_id))
        totals.cost_usd += float(cost or 0.0)
        if cost is None:
            totals.unpriced_calls += 1
        else:
            totals.priced_calls += 1
    totals.cost_usd = round(totals.cost_usd, 6)
    return totals


def _window(stmt: Any, column: Any, since: datetime | None, until: datetime | None) -> Any:
    if since is not None:
        stmt = stmt.where(column >= since)
    if until is not None:
        stmt = stmt.where(column <= until)
    return stmt


async def recount_costs(
    *,
    since: datetime | None = None,
    until: datetime | None = None,
    dry_run: bool = True,
    mode: str = "unpriced",
) -> dict[str, Any]:
    """Пересчитать цены фактов по текущим прайсам.

    ``mode="unpriced"`` (кнопка админки) — трогаем только строки без цены:
    уже оценённое не переписываем, повторный прогон ничего не меняет.
    ``mode="all"`` (CLI/владелец, только явным флагом) — переоцениваем всё
    окно; ``added_usd`` тогда дельта к прежней цене.

    Арендатор берётся из контекста (`current_tenant`), как в остальных
    запросах: RLS сам ограничит выборку. `dry_run=False` — записать.
    """
    if mode not in ("unpriced", "all"):
        raise ValueError(f"cost_recount: неизвестный режим {mode!r}")
    result = RecountResult(dry_run=dry_run, mode=mode)
    only_unpriced = mode == "unpriced"

    async with session_scope() as session:
        media_stmt = _window(select(MediaCall), MediaCall.created_at, since, until).order_by(
            MediaCall.id
        )
        llm_stmt = _window(select(LlmCall), LlmCall.created_at, since, until).order_by(LlmCall.id)
        media_rows = (await session.execute(media_stmt)).scalars().all()
        llm_rows = (await session.execute(llm_stmt)).scalars().all()
        if only_unpriced:
            # Кандидаты кнопки «пересчитать»: только строки без цены.
            media_rows = [r for r in media_rows if r.unpriced]
            llm_rows = [r for r in llm_rows if r.cost_usd is None or float(r.cost_usd) <= 0.0]
        result.scanned = len(media_rows) + len(llm_rows)

        for row in media_rows:
            cost, unit, unpriced = media_compute_cost(
                row.provider or "",
                row.model or "",
                float(row.units or 0.0),
                row.unit or "",
            )
            if unpriced:
                result.still_unpriced += 1
                continue
            old = float(row.cost_usd or 0.0)
            delta = cost - old
            if row.unpriced or abs(delta) > _EPS:
                result.priced += 1
                result.added_usd += delta
                if not dry_run:
                    row.cost_usd = cost
                    row.unpriced = False
                    row.unit = unit or row.unit

        for row in llm_rows:
            old = float(row.cost_usd or 0.0)
            usage = {
                "prompt_tokens": row.prompt_tokens,
                "completion_tokens": row.completion_tokens,
                "total_tokens": row.total_tokens,
            }
            cost, _pt, _ct, _tt, unbilled = llm_compute_cost(
                usage, model=row.model or "", served_model=row.served_model or ""
            )
            zero_rate = llm_price_for(row.model or "", row.served_model or "") == (0.0, 0.0)
            if unbilled or zero_rate:
                # Нет usage или модель по-прежнему вне прайса — строка так и
                # остаётся без цены (NULL), ноль не подставляем.
                result.still_unpriced += 1
                continue
            if row.cost_usd is None or abs(cost - old) > _EPS:
                result.priced += 1
                result.added_usd += cost - old
                if not dry_run:
                    row.cost_usd = cost

        # session_scope коммитит сам; при dry_run изменения не выставлялись.

    verb = "пересчитано" if not dry_run else "будет пересчитано"
    logger.info(
        "cost_recount[{}]: {} — строк {}, из них расценено/уточнено {}, без цены {}, "
        "прирост ${:.6f}",
        mode,
        verb,
        result.scanned,
        result.priced,
        result.still_unpriced,
        result.added_usd,
    )
    return result.to_dict()
