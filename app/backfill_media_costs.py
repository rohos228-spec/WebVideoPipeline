"""Пересчёт стоимости медиа-вызовов, которые записались без цены.

Зачем. До 21.10.2026 у ``outsee:gpt-image-2`` и ``outsee:nano-banana-2`` в
прайсе стояла цена ``null``. Строки вызовов легли с ``unpriced=true`` и
``cost_usd=0``. Прайс заполнили, но история осталась нулевой — отчёт расходов
занижен ровно на эти генерации, и «сколько стоят картинки» узнать неоткуда.

Что делает. Берёт строки ``media_calls`` с ``unpriced=true``, считает цену
текущим прайсом (``media_ledger.compute_cost``) и, если цена теперь известна,
записывает ``cost_usd`` и снимает флаг. Строки, для которых цены по-прежнему
нет, остаются ``unpriced`` — врать нулём нельзя.

Запуск (внутри контейнера, где лежит пакет ``app``):

    python -m app.backfill_media_costs           # только показать
    python -m app.backfill_media_costs --apply   # записать

Повторный запуск безопасен: после ``--apply`` строк с известной ценой уже не
будет ``unpriced``, останутся только те, что и должны.
"""

from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict

from loguru import logger
from sqlalchemy import select

from app.db import session_scope
from app.models import MediaCall
from app.services.media_ledger import compute_cost


async def backfill(apply: bool) -> int:
    updated = 0
    total_usd = 0.0
    still_unpriced: dict[str, int] = defaultdict(int)

    async with session_scope() as session:
        rows = (
            (
                await session.execute(
                    select(MediaCall).where(MediaCall.unpriced.is_(True)).order_by(MediaCall.id)
                )
            )
            .scalars()
            .all()
        )
        logger.info("backfill: строк без цены — {}", len(rows))

        for row in rows:
            cost, unit, unpriced = compute_cost(
                row.provider or "",
                row.model or "",
                float(row.units or 0.0),
                row.unit or "",
            )
            if unpriced:
                still_unpriced[f"{row.provider}:{row.model}"] += 1
                continue
            updated += 1
            total_usd += cost
            if apply:
                row.cost_usd = cost
                row.unpriced = False
                row.unit = unit or row.unit

        if apply and updated:
            await session.commit()

    verb = "пересчитано" if apply else "будет пересчитано"
    logger.info("backfill: {} строк, сумма ${:.4f}", verb, total_usd)
    if still_unpriced:
        logger.warning("backfill: цены по-прежнему нет у {}", dict(still_unpriced))
    if not apply:
        logger.warning("backfill: это примерка, для записи добавь --apply")
    print(
        f"{verb}: {updated} строк на ${total_usd:.4f}; без цены осталось: "
        f"{sum(still_unpriced.values())} ({dict(still_unpriced) or '—'})"
    )
    return updated


def main() -> None:
    parser = argparse.ArgumentParser(description="Пересчёт стоимости медиа-вызовов без цены")
    parser.add_argument("--apply", action="store_true", help="записать результат в базу")
    parser.add_argument(
        "--tenant",
        action="append",
        default=[],
        metavar="UUID",
        help=(
            "арендатор; можно повторять. Без флага скрипт работает в режиме "
            "владельца и видит только строки без арендатора (RLS)"
        ),
    )
    args = parser.parse_args()

    from app.services.tenant import set_tenant

    tenants: list[str | None] = list(args.tenant) or [None]
    for tenant in tenants:
        if tenant:
            set_tenant(tenant)
            logger.info("backfill: арендатор {}", tenant)
        asyncio.run(backfill(apply=args.apply))
    set_tenant(None)


if __name__ == "__main__":
    main()
