"""Пересчёт стоимости записанных вызовов, у которых цены не было.

Зачем. До 21.10.2026 у ``outsee:gpt-image-2`` и ``outsee:nano-banana-2`` в
прайсе стояла цена ``null``. Строки вызовов легли с ``unpriced=true`` и
``cost_usd=0``. Прайс заполнили, но история осталась нулевой — отчёт расходов
занижен ровно на эти генерации, и «сколько стоят картинки» узнать неоткуда.

Что делает. Пересчитывает факты текущим прайсом: ``media_calls`` (цена из
``media_prices.json``) и ``llm_calls`` (цена из токенов и ``llm_prices.json``).
Логика живёт в ``app/services/cost_recount.py`` — её же зовёт админ-ручка
``POST /api/admin/costs/recount``. Строки, для которых цены по-прежнему нет,
остаются ``unpriced``: врать нулём нельзя.

Запуск (внутри контейнера, где лежит пакет ``app``):

    python -m app.backfill_media_costs           # только показать
    python -m app.backfill_media_costs --apply   # записать
    python -m app.backfill_media_costs --from 2026-10-01 --to 2026-10-31
    python -m app.backfill_media_costs --tenant <uuid> --apply

Повторный запуск безопасен: уже расценённые строки с той же ценой не
трогаются, прирост ``added_usd`` во второй раз будет нулевым.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime

from loguru import logger

from app.services.cost_recount import recount_costs


def _parse_day(raw: str | None) -> datetime | None:
    """``YYYY-MM-DD`` → datetime в UTC; пусто — окно не ограничено."""
    if not raw:
        return None
    return datetime.strptime(raw, "%Y-%m-%d").replace(tzinfo=UTC).replace(tzinfo=None)


async def backfill(
    apply: bool,
    *,
    since: datetime | None = None,
    until: datetime | None = None,
    mode: str = "unpriced",
) -> int:
    """Пересчитать факты в окне. Возвращает число расценённых строк."""
    result = await recount_costs(since=since, until=until, dry_run=not apply, mode=mode)
    verb = "пересчитано" if apply else "будет пересчитано"
    print(
        f"{verb}: {result['priced']} строк на ${result['added_usd']:.4f}; "
        f"просмотрено {result['scanned']}; без цены осталось: {result['still_unpriced']}"
    )
    if not apply:
        logger.warning("backfill: это примерка, для записи добавь --apply")
    return int(result["priced"])


def main() -> None:
    parser = argparse.ArgumentParser(description="Пересчёт стоимости вызовов без цены")
    parser.add_argument("--apply", action="store_true", help="записать результат в базу")
    parser.add_argument(
        "--all",
        action="store_true",
        help="переоценить всё окно, а не только строки без цены (вызовы могут подорожать/подешеветь)",
    )
    parser.add_argument("--from", dest="since", metavar="YYYY-MM-DD", help="начало окна (включительно)")
    parser.add_argument("--to", dest="until", metavar="YYYY-MM-DD", help="конец окна (включительно)")
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
    since = _parse_day(args.since)
    until = _parse_day(args.until)
    mode = "all" if args.all else "unpriced"

    from app.services.tenant import set_tenant

    tenants: list[str | None] = list(args.tenant) or [None]
    for tenant in tenants:
        if tenant:
            set_tenant(tenant)
            logger.info("backfill: арендатор {}", tenant)
        asyncio.run(backfill(apply=args.apply, since=since, until=until, mode=mode))
    set_tenant(None)


if __name__ == "__main__":
    main()
