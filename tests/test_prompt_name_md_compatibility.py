"""Тесты на согласованность сохранения и разрешения имён промптов (.md vs без .md)."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base, PromptLibraryEntry
from app.services import prompt_store
from app.services.prompt_library import prompt_exists, read_prompt
from app.services.prompt_store import _name_candidates


def test_name_candidates_both_directions():
    assert _name_candidates("my_prompt") == ["my_prompt", "my_prompt.md"]
    assert _name_candidates("my_prompt.md") == ["my_prompt.md", "my_prompt"]
    assert _name_candidates("") == []


def test_prompt_store_resolve_and_exists_with_md_discrepancy():
    prompt_store.reset_cache()
    # Имитируем ситуацию с сервера: промпт сохранён в базе с именем '06_plastilin.md'
    prompt_store._CACHE[(None, "", None, "img_pr", "06_plastilin_prompt_volumetric_DB03.11.26.md")] = (
        "plastilin volumetric text"
    )

    # 1. Поиск без .md
    assert (
        prompt_store.resolve("img_pr", "06_plastilin_prompt_volumetric_DB03.11.26")
        == "plastilin volumetric text"
    )
    # 2. Поиск с .md
    assert (
        prompt_store.resolve("img_pr", "06_plastilin_prompt_volumetric_DB03.11.26.md")
        == "plastilin volumetric text"
    )

    # 3. prompt_exists работает в обе стороны
    assert prompt_exists("img_pr", "06_plastilin_prompt_volumetric_DB03.11.26") is True
    assert prompt_exists("img_pr", "06_plastilin_prompt_volumetric_DB03.11.26.md") is True

    # 4. read_prompt работает в обе стороны
    assert read_prompt("img_pr", "06_plastilin_prompt_volumetric_DB03.11.26") == "plastilin volumetric text"
    assert read_prompt("img_pr", "06_plastilin_prompt_volumetric_DB03.11.26.md") == "plastilin volumetric text"

    prompt_store.reset_cache()


@pytest.mark.asyncio
async def test_prompt_store_refresh_and_save_normalizes_md():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_maker() as session:
        # Вставляем строку напрямую с суффиксом .md, как это произошло на сервере
        row = PromptLibraryEntry(
            tenant_id=None,
            brand="",
            project_id=None,
            step_code="img_pr",
            name="06_plastilin_prompt_volumetric_DB03.11.26.md",
            text="stop motion clay style rules",
        )
        session.add(row)
        await session.commit()

        # Делаем refresh
        await prompt_store.refresh(session)

        # Проверяем, что резолвер видит оба варианта
        assert prompt_store.resolve("img_pr", "06_plastilin_prompt_volumetric_DB03.11.26") == "stop motion clay style rules"
        assert prompt_store.resolve("img_pr", "06_plastilin_prompt_volumetric_DB03.11.26.md") == "stop motion clay style rules"

        # Теперь сохраняем поверх через prompt_store.save
        await prompt_store.save(
            session,
            "img_pr",
            "06_plastilin_prompt_volumetric_DB03.11.26.md",
            "updated clay style rules",
        )
        await session.commit()

        # Имя нормализовано, текст обновлён
        assert prompt_store.resolve("img_pr", "06_plastilin_prompt_volumetric_DB03.11.26") == "updated clay style rules"
        assert prompt_store.resolve("img_pr", "06_plastilin_prompt_volumetric_DB03.11.26.md") == "updated clay style rules"

    prompt_store.reset_cache()
    await engine.dispose()
