"""Промт-библиотека в базе: чей промт побеждает и почему именно этот.

Библиотека живёт в `prompts/` — каталоге вне git. Для одной машины это
удобно, в SaaS не работает: диска узла у клиента нет, а «поменять промпт на
более лучший» это ровно то, зачем он пришёл (`docs/SAAS-PIVOT.md` §9.4).

Проверяется главное свойство переопределения — порядок. Настройка, которая
применяется через раз, хуже отсутствующей: её невозможно отладить, потому что
она работает.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base
from app.services import prompt_store
from app.services.prompt_store import PromptScope
from app.settings import settings

SYSTEM = PromptScope()
BRAND = PromptScope(brand="videostudio")
TENANT = "11111111-1111-4111-8111-111111111111"


@pytest.fixture
async def db(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "allow_unisolated_tenants", True)
    monkeypatch.setattr(settings, "studio_brand", "videostudio")
    prompt_store.reset_cache()
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'p.db'}", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    prompt_store.reset_cache()
    await engine.dispose()


async def test_nothing_in_the_base_means_the_disk_still_rules(db) -> None:
    """Пока библиотека не загружена, всё работает ровно как работало.

    Это условие безопасности правки: она не должна менять поведение там, где
    её не включали.
    """
    assert prompt_store.resolve("plan", "default") is None
    assert not prompt_store.loaded()


async def test_tenant_beats_brand_beats_system(db) -> None:
    """Порядок переопределения — от частного к общему, первый побеждает.

    Каждый уровень отвечает на свой вопрос: системный «как правильно», бренд
    «как принято у нас», арендатор «как хочу я». Схлопнуть их значит однажды
    переписывать, а не добавлять строку.
    """
    async with db() as s:
        await prompt_store.save(s, "plan", "default", "системный", scope=SYSTEM)
        await s.commit()
    assert prompt_store.resolve("plan", "default", SYSTEM) == "системный"

    async with db() as s:
        await prompt_store.save(s, "plan", "default", "брендовый", scope=BRAND)
        await s.commit()
    assert prompt_store.resolve("plan", "default", BRAND) == "брендовый"
    # Системный при этом на месте: переопределение не затирает нижний уровень.
    assert prompt_store.resolve("plan", "default", SYSTEM) == "системный"

    tenant_scope = PromptScope(tenant_id=TENANT, brand="videostudio")
    async with db() as s:
        await prompt_store.save(s, "plan", "default", "мой", scope=tenant_scope)
        await s.commit()
    assert prompt_store.resolve("plan", "default", tenant_scope) == "мой"
    assert prompt_store.resolve("plan", "default", BRAND) == "брендовый"


async def test_project_beats_tenant(db) -> None:
    """«Как в этом ролике» перебивает «как хочу я» — иначе проектный уровень бессмыслен."""
    tenant_scope = PromptScope(tenant_id=TENANT)
    project_scope = PromptScope(tenant_id=TENANT, project_id=7)
    async with db() as s:
        await prompt_store.save(s, "img_pr", "default", "мой обычный", scope=tenant_scope)
        await prompt_store.save(s, "img_pr", "default", "для этого ролика", scope=project_scope)
        await s.commit()
    assert prompt_store.resolve("img_pr", "default", project_scope) == "для этого ролика"
    assert prompt_store.resolve("img_pr", "default", tenant_scope) == "мой обычный"


async def test_a_tenant_cannot_write_into_the_system_level(db) -> None:
    """Уровень выбирает область, а не вызывающий.

    «Сохранить как системный, будучи арендатором» — не настройка, а ошибка:
    один клиент поменял бы промт всем остальным.
    """
    async with db() as s:
        await prompt_store.save(s, "plan", "default", "системный", scope=SYSTEM)
        await prompt_store.save(
            s, "plan", "default", "мой", scope=PromptScope(tenant_id=TENANT, brand="videostudio")
        )
        await s.commit()
    assert prompt_store.resolve("plan", "default", SYSTEM) == "системный"


async def test_refresh_rebuilds_the_cache_from_the_base(db) -> None:
    """Кэш это производная, а не источник: перезагрузка обязана его пересобрать."""
    async with db() as s:
        await prompt_store.save(s, "script", "default", "текст", scope=SYSTEM)
        await s.commit()
    prompt_store.reset_cache()
    assert prompt_store.resolve("script", "default", SYSTEM) is None

    async with db() as s:
        count = await prompt_store.refresh(s)
    assert count == 1
    assert prompt_store.loaded()
    assert prompt_store.resolve("script", "default", SYSTEM) == "текст"


async def test_disk_import_does_not_overwrite_edited_prompts(db) -> None:
    """Файл с диска не затирает то, что уже правили в базе.

    Диск мог остаться от прошлой версии, а промт в базе — это работа
    человека. Затереть её файлом без вопроса значит потерять её молча.
    """
    async with db() as s:
        await prompt_store.save(s, "plan", "default", "правленый в базе", scope=SYSTEM)
        await s.commit()

    async with db() as s:
        stats = await prompt_store.import_from_disk(s)
        await s.commit()

    assert prompt_store.resolve("plan", "default", SYSTEM) == "правленый в базе"

    # Пропуск считается ТОЛЬКО если на диске есть тот самый промт, который
    # тест положил в базу. Прежнее условие смотрело на `stats["seen"]` — «файлы
    # вообще нашлись», — и на чистом чекауте давало ложное падение: библиотека
    # намеренно вне git (`.gitignore: prompts/*`), но часть её в git есть
    # (`scene_design/` и прочее). То есть файлы находились, а `01_plan` среди
    # них не было, и `skipped` честно оставался нулём. Поймано симуляцией CI на
    # свежем клоне; на машине владельца, где библиотека полная, тест был зелён.
    from app.services.prompt_library import prompt_path

    if prompt_path("plan", "default").is_file():
        assert stats["skipped"] >= 1, (
            "файл prompts/01_plan/default.md на диске есть, но импорт его не пропустил — "
            "значит правка в базе была бы затёрта"
        )


async def test_read_prompt_prefers_the_base(db, monkeypatch) -> None:
    """Сквозная проверка: `read_prompt` берёт из базы, не трогая диск.

    Это тот же шов, что у книги Excel: боевой путь не должен зависеть от
    файла на диске узла.
    """
    from app.services.prompt_library import read_prompt

    async with db() as s:
        await prompt_store.save(s, "plan", "default", "из базы", scope=SYSTEM)
        await s.commit()

    def _boom(*a, **kw):
        raise AssertionError("промт прочитан с диска, хотя он есть в базе")

    monkeypatch.setattr("app.services.prompt_library.prompt_path", _boom)
    assert read_prompt("plan", "default") == "из базы"


async def test_source_says_whose_prompt_is_in_force(db) -> None:
    """«Правлю, а не меняется» — первый вопрос человека с проектной правкой.

    Отдать текст, не сказав, чей он, значит гарантировать этот вопрос.
    """
    from app.services.prompt_store import resolve_with_source

    tenant_scope = PromptScope(tenant_id=TENANT)
    project_scope = PromptScope(tenant_id=TENANT, project_id=5)

    async with db() as s:
        await prompt_store.save(s, "plan", "default", "системный", scope=SYSTEM)
        await s.commit()
    assert resolve_with_source("plan", "default", tenant_scope) == ("системный", "system")

    async with db() as s:
        await prompt_store.save(s, "plan", "default", "мой", scope=tenant_scope)
        await s.commit()
    assert resolve_with_source("plan", "default", tenant_scope) == ("мой", "tenant")

    async with db() as s:
        await prompt_store.save(s, "plan", "default", "для ролика", scope=project_scope)
        await s.commit()
    assert resolve_with_source("plan", "default", project_scope) == ("для ролика", "project")
    # Личный при этом на месте: проектный его не съел.
    assert resolve_with_source("plan", "default", tenant_scope) == ("мой", "tenant")


async def test_reset_removes_only_my_own_override(db) -> None:
    """«Вернуть как было» убирает СВОЮ строку, а не системную.

    Удалить чужую арендатор не может по построению: область та же, что и при
    записи.
    """
    tenant_scope = PromptScope(tenant_id=TENANT)
    async with db() as s:
        await prompt_store.save(s, "script", "default", "системный", scope=SYSTEM)
        await prompt_store.save(s, "script", "default", "мой", scope=tenant_scope)
        await s.commit()
    assert prompt_store.resolve("script", "default", tenant_scope) == "мой"

    async with db() as s:
        removed = await prompt_store.drop(s, "script", "default", scope=tenant_scope)
        await s.commit()
    assert removed is True
    assert prompt_store.resolve("script", "default", tenant_scope) == "системный"

    # Повторный сброс не ошибка: нечего убирать — значит уже как надо.
    async with db() as s:
        assert await prompt_store.drop(s, "script", "default", scope=tenant_scope) is False
