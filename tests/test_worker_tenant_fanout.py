"""Петля воркера после разрезания на «кому служим» и «служим».

Тело тика вынесено из `while True` в отдельную функцию, чтобы проход шёл от
имени арендатора. Перенос был механическим — отступы не менялись, — но
именно такие переносы ломаются тихо: забытый `nonlocal`, замыкание, которое
перестало замыкать, переменная из внешнего цикла. Всё это не видно ни ruff,
ни mypy: питон обнаружит их только на исполнении.

Изоляцию как таковую проверяет `tests/test_rls_postgres.py` на живом
Postgres — в SQLite политик нет вовсе. Здесь проверяется механика.
"""

from __future__ import annotations

import asyncio

import pytest

from app.settings import settings


async def test_worker_loop_survives_a_tick(monkeypatch, tmp_path) -> None:
    """Петля делает проход и не падает.

    Ловит ровно те поломки переноса, которые не видит статический анализ:
    петля стартует, доходит до первого `await asyncio.sleep(5)` и живёт.
    Пустая база — работы нет, побочных действий тоже.
    """
    from app.main import _run_worker_loop
    from app.telegram.noop_bot import get_worker_bot

    task = asyncio.create_task(_run_worker_loop(get_worker_bot(None)))
    # Хватает и доли секунды: тик до сна укладывается в миллисекунды, а
    # исключение в замыкании упало бы на первом же обращении.
    await asyncio.sleep(0.5)
    alive = not task.done()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert alive, "петля воркера умерла на первом тике"


async def test_owner_mode_serves_exactly_one_pass() -> None:
    """В режиме владельца арендатор один — `None`, и запросов не прибавилось.

    Это половина смысла правки: на машине владельца поведение обязано
    остаться прежним, иначе цена изоляции — сломанный работающий продукт.
    """
    from app.services.work_routing import _ordered

    assert not settings.is_postgres, "тест про режим владельца"
    assert _ordered([None]) == [None]
    assert _ordered([]) == []


async def test_owner_goes_first_and_tenants_are_stable() -> None:
    """Порядок обхода стабилен, владелец первый.

    При нехватке слотов параллели воркер обрывает обход. Случайный порядок
    означал бы, что кому-то не достаётся очереди никогда, — и заметить это
    можно было бы только по жалобе клиента.
    """
    from app.services.work_routing import _ordered

    rows = ["b0000000-0000-4000-8000-000000000000", None, "a0000000-0000-4000-8000-000000000000"]
    assert _ordered(rows) == [
        None,
        "a0000000-0000-4000-8000-000000000000",
        "b0000000-0000-4000-8000-000000000000",
    ]
    assert _ordered(rows) == _ordered(list(reversed(rows)))


async def test_routes_query_is_empty_without_work(tmp_path) -> None:
    """Пустая маршрутная таблица — пустой ответ, а не «все подряд»."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.models import Base
    from app.services.work_routing import tenants_with_active_work

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'routes.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        assert await tenants_with_active_work(session, ["planning"]) == []
        assert await tenants_with_active_work(session, []) == []
    await engine.dispose()
