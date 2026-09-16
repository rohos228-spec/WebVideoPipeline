"""Тесты сервиса `app.services.test_prompt`.

Покрываем то, что НЕ требует реального запуска браузера (ChatGPT /
outsee) — то есть: создание проекта, проверка локов (только один
тестовый цикл).
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base
from app.models import TestPromptProject as TPProject  # avoid pytest auto-collect of Test* class
from app.services.test_prompt import (
    create_test_project,
    get_running_project,
    is_busy,
)


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as s:
        yield s
    await engine.dispose()


@pytest.mark.asyncio
async def test_create_test_project_basic(session) -> None:
    p = await create_test_project(session, "My Test")
    assert p.id == 1
    assert p.slug == "my-test"
    assert p.name == "My Test"
    assert p.status == "idle"
    assert p.current_iter == 0
    assert p.visual_prompt is None
    assert p.system_prompt is None


@pytest.mark.asyncio
async def test_create_test_project_dedupe_slug(session) -> None:
    p1 = await create_test_project(session, "Same Name")
    p2 = await create_test_project(session, "Same Name")
    p3 = await create_test_project(session, "Same Name")
    assert p1.slug == "same-name"
    assert p2.slug == "same-name-2"
    assert p3.slug == "same-name-3"


@pytest.mark.asyncio
async def test_create_test_project_empty_name(session) -> None:
    with pytest.raises(ValueError):
        await create_test_project(session, "")
    with pytest.raises(ValueError):
        await create_test_project(session, "   ")


@pytest.mark.asyncio
async def test_create_test_project_cyrillic_slug(session) -> None:
    """Кириллица в имени — slug содержит её, т.к. regex `[а-я]` — это
    ожидаемое поведение (slug используется как имя папки на диске).
    """
    p = await create_test_project(session, "Тестик Кошки")
    # «тестик-кошки» (кириллица сохраняется).
    assert p.slug == "тестик-кошки"


@pytest.mark.asyncio
async def test_get_running_project_none(session) -> None:
    await create_test_project(session, "A")
    await create_test_project(session, "B")
    assert (await get_running_project(session)) is None


@pytest.mark.asyncio
async def test_get_running_project_picks_running(session) -> None:
    p_a = await create_test_project(session, "A")
    p_b = await create_test_project(session, "B")
    p_b.status = "running_gpt"
    await session.flush()
    running = await get_running_project(session)
    assert running is not None
    assert running.id == p_b.id

    # Симулируем что одновременно ещё один проект «в outsee» (по факту
    # лок должен предотвратить это, но get_running_project честно
    # вернёт любого).
    p_a.status = "running_outsee"
    await session.flush()
    running = await get_running_project(session)
    assert running is not None
    assert running.id in (p_a.id, p_b.id)


@pytest.mark.asyncio
async def test_is_busy(session) -> None:
    p = await create_test_project(session, "X")
    assert not is_busy(p)
    p.status = "running_gpt"
    assert is_busy(p)
    p.status = "running_outsee"
    assert is_busy(p)
    p.status = "waiting_critique"
    assert not is_busy(p)
    p.status = "idle"
    assert not is_busy(p)
    p.status = "stopped"
    assert not is_busy(p)


def test_data_dir_and_iter_dir() -> None:
    p = TPProject(
        id=7,
        slug="foo",
        name="Foo",
        status="idle",
        current_iter=0,
    )
    assert "test_prompts" in str(p.data_dir)
    assert str(p.data_dir).endswith("foo")
    assert p.iter_dir(1).name == "iter_001"
    assert p.iter_dir(42).name == "iter_042"



