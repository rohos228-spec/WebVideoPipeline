"""Списание Create-задачи по ФАКТУ, а не по оценке каталога.

До этого вкладка «Генерация» ставила холд по `kie_catalog.estimate_credits`
(или грубой оценке outsee) и закрывала его тем же числом: списание не было
связано ни с реальным вызовом провайдера, ни с проектом. Отсюда картина
«в кассе $13, а фактов на цент» — платили по каталогу, а журнал вызовов
оставался пустым или неполным.

Правильно так, как делает касса шага (`step_billing`): до вызова запомнить
свои строки, после — сложить `cost_usd` ровно по ним. Только вместо
id-границы журнала здесь id, собранные `media_ledger.capture_rows`: Create
не привязан к проекту, и «все строки выше отметки» захватило бы чужие
параллельные генерации.

`unpriced` не превращается в ноль: если у модели нет подтверждённой цены,
такая генерация в сумму не входит, а в memo честно написано, что часть
цены оценочная. Кредиты за это не списываются — выдуманную цифру в леджер
не пишем.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from loguru import logger

from app.services import media_ledger
from app.services.cost_recount import facts_for_rows
from app.services.credit_ledger import UnknownHold, settle_hold


@dataclass
class FactCharge:
    """Что списали и почему именно столько."""

    charged_usd: float = 0.0
    calls: int = 0
    unpriced_calls: int = 0
    ref_ids: list[int] = field(default_factory=list)
    memo: str = ""


async def charge_hold_by_fact(
    session: Any,
    hold_id: str,
    row_ids: list[int],
    *,
    est_usd: float,
    memo: str,
    ref_table: str = "media_calls",
) -> FactCharge:
    """Закрыть холд суммой фактических вызовов из ``row_ids``.

    Повторный вызов безопасен: `settle_hold` бросает ``UnknownHold`` на уже
    закрытый холд — это не ошибка задачи, а признак повторного расчёта.
    """
    # Строки могли отбиться на занятой базе (SQLite) — до счёта их надо долить.
    await media_ledger.flush_pending()

    totals = await facts_for_rows(session, row_ids)
    parts = [memo]
    if totals.unpriced_calls:
        parts.append(
            f"unpriced {totals.unpriced_calls} из {totals.calls}, "
            f"оценка каталога ${est_usd:.4f} не списана"
        )
        logger.warning(
            "create_billing: у {} строк без цены — списываем только подтверждённое (${:.4f})",
            totals.unpriced_calls,
            totals.cost_usd,
        )
    if totals.calls == 0:
        parts.append(f"факт не записан, оценка ${est_usd:.4f} не списана")
        logger.warning("create_billing: фактов по задаче нет — списание нулевое, оценка ${:.4f}", est_usd)
    charge = FactCharge(
        charged_usd=totals.cost_usd,
        calls=totals.calls,
        unpriced_calls=totals.unpriced_calls,
        ref_ids=totals.ref_ids,
        memo="; ".join(p for p in parts if p),
    )
    try:
        await settle_hold(
            session,
            hold_id,
            cost_usd=totals.cost_usd,
            ref_table=ref_table,
            ref_ids=totals.ref_ids,
            memo=charge.memo,
        )
    except UnknownHold:
        logger.warning("create_billing: холд {} уже закрыт — повторного списания нет", hold_id)
    return charge
