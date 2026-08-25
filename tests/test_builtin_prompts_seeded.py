"""Встроенные промты сеются в базу — иначе править их нечем.

Шаг разбора состава работает без файла: текст лежит в
`cast_extract.DEFAULT_PROMPT`. Но редактор показывает то, что видит
`prompt_store`, и без посева человек увидел бы «промтов не заведено» у шага,
который прямо сейчас работает.

Первая редакция материализовала текст ФАЙЛОМ на старте. На сервере это
невозможно: `prompts/` смонтирован только для чтения. Посев идёт в базу на
системный уровень — и ровно один раз: повторный старт не трогает то, что там
уже есть, иначе правка человека возвращалась бы к встроенному тексту после
каждого перезапуска.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base
from app.services import prompt_store
from app.services.builtin_prompts import builtin_prompts, seed_builtin_prompts
from app.services.pipeline_stages import STAGES
from app.services.prompt_library import STEP_FOLDERS, read_prompt
from app.services.prompt_store import PromptScope


@pytest_asyncio.fixture
async def db(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'seed.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    @asynccontextmanager
    async def scope():
        async with factory() as s:
            yield s
            await s.commit()

    prompt_store.reset_cache()
    yield scope
    prompt_store.reset_cache()
    await engine.dispose()


def test_cast_step_is_registered_and_editable() -> None:
    assert "cast" in STEP_FOLDERS
    cast_stage = next(s for s in STAGES if s.id == "cast")
    assert "cast" in cast_stage.prompt_steps, "промт разбора состава не объявлен в стадии"


@pytest.mark.asyncio
async def test_seed_puts_builtin_prompt_into_the_store(db) -> None:
    async with db() as s:
        assert await seed_builtin_prompts(s) == len(builtin_prompts())

    text = prompt_store.resolve("cast", "default", PromptScope())
    assert text == builtin_prompts()[("cast", "default")]
    # И той же дорогой, что идёт шаг.
    assert read_prompt("cast", "default") == text


@pytest.mark.asyncio
async def test_seed_is_idempotent_and_keeps_user_edits(db) -> None:
    """Главное свойство: перезапуск не затирает правку."""
    async with db() as s:
        await seed_builtin_prompts(s)
        await prompt_store.save(s, "cast", "default", "мой промт", scope=PromptScope())

    async with db() as s:
        assert await seed_builtin_prompts(s) == 0, "повторный посев не должен ничего писать"

    assert prompt_store.resolve("cast", "default", PromptScope()) == "мой промт"


@pytest.mark.asyncio
async def test_seeded_prompt_survives_refresh(db) -> None:
    """Посев — в базу, а не только в кэш: после `refresh` он на месте."""
    async with db() as s:
        await seed_builtin_prompts(s)
    prompt_store.reset_cache()
    async with db() as s:
        await prompt_store.refresh(s)
    assert prompt_store.resolve("cast", "default", PromptScope()) is not None


@pytest.mark.asyncio
async def test_import_overwrite_reads_the_disk_not_the_cache(db, tmp_path, monkeypatch) -> None:
    """`import_from_disk(overwrite=True)` берёт файл, а не то, что уже в базе.

    Первая досинхронизация на сервере «прошла» (135 записано) и ничего не
    изменила: импорт читал через `read_prompt`, а тот идёт «база первая» и
    вернул старый кэш. База была переписана базой.
    """
    from app.services import prompt_library

    monkeypatch.setattr(prompt_library, "PROMPTS_ROOT", tmp_path)
    (tmp_path / prompt_library.STEP_FOLDERS["plan"]).mkdir(parents=True)
    (tmp_path / prompt_library.STEP_FOLDERS["plan"] / "default.md").write_text("с диска", encoding="utf-8")

    async with db() as s:
        await prompt_store.save(s, "plan", "default", "старое из базы", scope=PromptScope())
    assert prompt_library.read_prompt("plan", "default") == "старое из базы"

    async with db() as s:
        stats = await prompt_store.import_from_disk(s, overwrite=True)
    assert stats["written"] >= 1
    assert prompt_store.resolve("plan", "default", PromptScope()) == "с диска"


@pytest.mark.asyncio
async def test_hero_style_is_read_from_the_store(db) -> None:
    """Стиль, сохранённый в базу (через редактор), шаг видит без файла на диске.

    `_read_hero_style` читал только диск. На сервере диск только для чтения,
    и стиль из редактора шаг бы не увидел — предупреждал «не найден на
    диске» про промт, который лежит в базе.
    """
    from types import SimpleNamespace

    from app.orchestrator.steps.generate_hero import _read_hero_style

    async with db() as s:
        await prompt_store.save(s, "hero_style", "default", "мой стиль из базы", scope=PromptScope())

    project = SimpleNamespace(id=1, prompt_overrides={}, meta={})
    assert _read_hero_style(project) == "мой стиль из базы"


@pytest.mark.asyncio
async def test_hero_style_default_is_seeded(db) -> None:
    async with db() as s:
        await seed_builtin_prompts(s)
    assert prompt_store.resolve("hero_style", "default", PromptScope())
