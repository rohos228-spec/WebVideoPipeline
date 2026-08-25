"""Пустой шаг предметов не должен качать статус hero_ready ↔ generating_items.

Парный к `test_hero_empty_skip_no_loop.py`. Тот же дефект, соседний шаг: для
героя его нашли и закрыли меткой `hero_skipped_empty`, для предметов дыра
осталась и вскрылась живым прогоном 2026-08-25.

Механика. `item_descriptions` пуст, и две стороны читают это поле
противоположно:

* `generate_items` — «работы нет, значит сделано» и ставит `items_ready`;
* `_items_step_required` — «шага нет вовсе», поэтому `compute_actual_status`
  никогда не возвращает `items_ready`, только `hero_ready`.

Дальше страж откатывает статус как «опережающий данные», авто-продвижение
одобряет `hero_ready` и снова запускает `generating_items`. Круг замыкается,
каждые пять секунд, без конца.

Коварство в том, что это не падение: ничего не бросает исключений, счётчик
`step_failure_policy` не растёт, в паузу проект не уходит, в интерфейсе
бесконечное «идёт работа». На живом сервере он крутился семь минут и
остановился бы только руками.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base, Project, ProjectStatus
from app.services.project_state import (
    _items_skipped_empty,
    _items_step_required,
    compute_actual_status,
)


@pytest.fixture
async def mem_db(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    @asynccontextmanager
    async def _scope():
        async with factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    monkeypatch.setattr("app.db.session_scope", _scope)
    yield _scope
    await engine.dispose()


def _project(**kw) -> Project:
    """Проект, дошедший до предметов: с планом, закадром и кадрами.

    Без них `compute_actual_status` останавливается на `new` задолго до ветки
    предметов, и проверка ничего бы не проверяла.
    """
    base = {
        "slug": f"items-{uuid.uuid4().hex[:8]}",
        "topic": "тема",
        "status": ProjectStatus.generating_items,
        "hero_mode": "no_hero",
        "item_descriptions": [],
        "general_plan": "крючок, развитие, финал" * 20,
        "script_text": "закадровый текст" * 20,
        "meta": {"split_completed": True},
    }
    meta_extra = kw.pop("meta", None)
    base.update(kw)
    if meta_extra:
        base["meta"] = {**base["meta"], **meta_extra}
    return Project(**base)


async def _with_frames(session, project: Project, n: int = 12) -> None:
    """Кадры в БД — как после разбивки на живом прогоне."""
    from app.models import Frame

    for i in range(1, n + 1):
        session.add(Frame(project_id=project.id, number=i, voiceover_text=f"кадр {i}"))
    await session.flush()


@pytest.mark.asyncio
async def test_step_marks_the_empty_skip(mem_db) -> None:
    """Холостой прогон обязан оставить метку, а не просто сменить статус."""
    from app.orchestrator.steps import generate_items

    async with mem_db() as session:
        p = _project()
        session.add(p)
        await session.flush()
        await _with_frames(session, p)

        await generate_items.run(session, p, None)

        assert p.status is ProjectStatus.items_ready
        assert _items_skipped_empty(p), (
            "без метки items_skipped_empty статус откатится и шаг запустится снова"
        )


@pytest.mark.asyncio
async def test_marked_skip_is_confirmed_by_compute_actual_status(mem_db) -> None:
    """Ключевое место: после метки фактический статус больше не ниже.

    Именно расхождение `items_ready` (поставил шаг) и `hero_ready` (вернул
    расчёт) и раскручивало цикл.
    """
    async with mem_db() as session:
        p = _project(status=ProjectStatus.items_ready, meta={"items_skipped_empty": True})
        session.add(p)
        await session.flush()
        await _with_frames(session, p)

        actual = await compute_actual_status(session, p)

        assert actual is ProjectStatus.items_ready, (
            f"расчёт вернул {actual.value} вместо items_ready — статус откатится, "
            "и авто-продвижение снова запустит шаг"
        )


@pytest.mark.asyncio
async def test_without_the_mark_the_status_still_rolls_back(mem_db) -> None:
    """Обратная сторона: без метки откат сохраняется.

    Метка — единственное, что отличает «шаг честно прошёл вхолостую» от
    «статус проставили, а работы не было». Второе откатывать по-прежнему надо,
    иначе проверка превратилась бы в «всегда соглашаться».
    """
    async with mem_db() as session:
        p = _project(status=ProjectStatus.items_ready)
        session.add(p)
        await session.flush()
        await _with_frames(session, p)

        actual = await compute_actual_status(session, p)

        assert actual is not ProjectStatus.items_ready


@pytest.mark.asyncio
async def test_real_descriptions_are_untouched(mem_db) -> None:
    """Когда предметы реально заданы, метка не ставится и шаг делает работу.

    Без этого случая правка означала бы «шаг всегда пропускаем».
    """
    async with mem_db() as session:
        p = _project(item_descriptions=["ржавая банка кротовухи", "чек вместо карты"])
        session.add(p)
        await session.flush()
        await _with_frames(session, p)

        assert _items_step_required(p) is True
        assert _items_skipped_empty(p) is False

        # Артефактов нет — расчёт обязан держать статус на hero_ready.
        actual = await compute_actual_status(session, p)
        assert actual is not ProjectStatus.items_ready


@pytest.mark.asyncio
async def test_full_cycle_settles(mem_db) -> None:
    """Сквозная проверка: прогон шага и расчёт сходятся, а не спорят.

    Это и есть тот инвариант, который был нарушен: статус после шага не должен
    оказываться выше того, что подтверждает расчёт. Иначе получается качели.
    """
    from app.orchestrator.steps import generate_items

    async with mem_db() as session:
        p = _project()
        session.add(p)
        await session.flush()
        await _with_frames(session, p)

        await generate_items.run(session, p, None)
        after_step = p.status

        actual = await compute_actual_status(session, p)

        assert actual is after_step, (
            f"шаг оставил {after_step.value}, расчёт настаивает на {actual.value} — "
            "статус будет качаться между ними бесконечно"
        )
