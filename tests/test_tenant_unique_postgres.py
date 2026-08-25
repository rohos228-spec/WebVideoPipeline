"""Уникальные ключи промт-таблиц не пересекают арендаторов (живой Postgres).

Первое сохранение промта из редактора на сервере — 500 без строки в журнале.
`library_items UNIQUE (kind, key)` и `master_prompts UNIQUE (key, version)`
не знали арендатора. Системная строка (tenant_id IS NULL) под сессией
арендатора невидима — RLS прячет её честно, — код вставляет «новую» с тем же
ключом, Postgres отбивает. На SQLite RLS нет, строка видна, код её обновляет;
суита зелёная.

Проверить это можно только там, где решает сервер: на Postgres с ролью без
суперправ и миграциями до head. Пропускается без `TEST_DATABASE_URL` — как
и `test_rls_postgres.py`, откуда взята фикстура.
"""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import text

import app.db  # noqa: F401 — регистрирует слушатель `SET LOCAL app.tenant_id`; без него RLS в тесте не участвует

TEST_DB_URL = os.environ.get("TEST_DATABASE_URL", "").strip()

pytestmark = pytest.mark.skipif(not TEST_DB_URL, reason="TEST_DATABASE_URL не задан — нужен живой Postgres")


@pytest.fixture
async def pg():
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    from app.db_migrations import upgrade_to_head_sync
    from app.settings import settings

    prev = settings.database_url
    settings.database_url = TEST_DB_URL
    engine = create_async_engine(TEST_DB_URL, poolclass=NullPool)
    try:
        upgrade_to_head_sync()
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()
        settings.database_url = prev


async def test_tenant_can_save_library_item_shadowed_by_system_row(pg) -> None:
    """Системная строка есть; арендатор сохраняет под тем же ключом — успех."""
    from app.services import local_library as lib
    from app.services.tenant import tenant_scope

    key = f"prompts/01_plan/{uuid.uuid4().hex[:8]}.md"
    tenant = str(uuid.uuid4())

    with tenant_scope(None):
        async with pg() as s:
            await lib.create_or_update_item(
                s, kind="prompt", key=key, title="sys", file_path=key, content="системный"
            )
            await s.commit()

    with tenant_scope(tenant):
        async with pg() as s:
            # Страховка самого теста: изоляция обязана быть включена в сессии.
            # Первый репро этой ошибки был зелёным впустую — слушатель, который
            # ставит GUC, не был импортирован, и арендатор видел всё.
            guc = (await s.execute(text("select current_setting('app.tenant_id', true)"))).scalar()
            assert guc == tenant, f"изоляция не включилась (GUC={guc!r}) — тест ничего не проверяет"
            item, version, _ = await lib.create_or_update_item(
                s, kind="prompt", key=key, title="mine", file_path=key, content="мой"
            )
            await s.commit()
            assert item.tenant_id == tenant, "строка арендатора должна быть его, а не системной"
            assert version.content == "мой"

        # Повторное сохранение того же арендатора — обновление, не второй дубль.
        async with pg() as s:
            item2, version2, _ = await lib.create_or_update_item(
                s, kind="prompt", key=key, title="mine", file_path=key, content="мой-2"
            )
            await s.commit()
            assert item2.id == item.id
            assert version2.version == 2


async def test_tenant_master_prompt_does_not_collide_with_system(pg) -> None:
    """`sync_step_prompt_to_db` под арендатором при существующей системной строке."""
    from app.services.prompts import sync_step_prompt_to_db
    from app.services.tenant import tenant_scope

    tenant = str(uuid.uuid4())

    with tenant_scope(None):
        async with pg() as s:
            assert await sync_step_prompt_to_db(s, "plan", "системный план") is True
            await s.commit()

    with tenant_scope(tenant):
        async with pg() as s:
            assert await sync_step_prompt_to_db(s, "plan", "план арендатора") is True
            await s.commit()


async def test_old_cross_tenant_constraints_are_gone(pg) -> None:
    """Ревизия 0013 сняла общие ключи и поставила частичные индексы."""
    from sqlalchemy import text

    async with pg() as s:
        names = {
            r[0]
            for r in (
                await s.execute(
                    text(
                        "select conname from pg_constraint where conrelid in "
                        "('library_items'::regclass, 'master_prompts'::regclass) and contype = 'u'"
                    )
                )
            ).all()
        }
        assert "uq_library_item_kind_key" not in names
        assert "uq_prompt_key_version" not in names
        idx = {
            r[0]
            for r in (
                await s.execute(
                    text(
                        "select indexname from pg_indexes where tablename in ('library_items','master_prompts')"
                    )
                )
            ).all()
        }
        for table in ("library_items", "master_prompts"):
            assert f"uq_{table}_tenant_scoped" in idx, f"{table}: нет индекса по арендатору"
            assert f"uq_{table}_system_level" in idx, f"{table}: нет индекса системного уровня"
