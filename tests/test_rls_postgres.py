"""Изоляция арендаторов на живом Postgres — то, что нельзя проверить иначе.

Всё остальное про RLS проверяется на SQLite косвенно: список таблиц, наличие
колонки, отказ работать без изоляции. Но сам вопрос «действительно ли чужая
строка невидима» решается только сервером, потому что решает его сервер.

Тест пропускается, если Postgres не подан. Подать так::

    podman run -d --name vp-pg -p 5432:5432 \\
        -e POSTGRES_PASSWORD=vp -e POSTGRES_USER=vp -e POSTGRES_DB=vp postgres:16
    pip install asyncpg psycopg[binary]
    podman exec vp-pg psql -U vp -d vp \\
        -c "create role app login password 'app'" \\
        -c "grant all on schema public to app"
    TEST_DATABASE_URL=postgresql+asyncpg://app:app@127.0.0.1/vp pytest tests/test_rls_postgres.py

**Роль в URL не должна быть суперпользователем.** `POSTGRES_USER` образа —
как раз суперпользователь, а RLS его не касается по определению: тесты
изоляции прошли бы «зелёными», не проверив ровно ничего. Это худший исход из
возможных, поэтому он вынесен в отдельную проверку, которая падает первой.
В URL выше поэтому стоит `app`, а не `vp`: роль обычная, схему она создаёт
себе сама при прогоне миграций и потому владеет таблицами — а владельца
достаёт как раз `FORCE ROW LEVEL SECURITY`.

Слаги проектов уникальны в каждом прогоне: тест, который проходит только на
пустой базе, перестают запускать, а пересоздание базы забывают.
"""

from __future__ import annotations

import os
import uuid

import pytest

TEST_DB_URL = os.environ.get("TEST_DATABASE_URL", "").strip()

pytestmark = pytest.mark.skipif(
    not TEST_DB_URL,
    reason="TEST_DATABASE_URL не задан — живого Postgres нет",
)


@pytest.fixture
async def pg_engine():
    """Свежая схема на живом Postgres: миграции с нуля до head."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    from app.db_migrations import upgrade_to_head_sync
    from app.settings import settings

    prev = settings.database_url
    settings.database_url = TEST_DB_URL
    engine = create_async_engine(TEST_DB_URL, poolclass=NullPool)
    try:
        upgrade_to_head_sync()
        yield engine, async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()
        settings.database_url = prev


async def test_role_is_not_superuser(pg_engine) -> None:
    """Суперпользователь обходит RLS — и все остальные тесты становятся ложью.

    Проверка стоит первой намеренно: «зелёный» прогон под `postgres`
    означал бы, что изоляция не проверена вовсе.
    """
    from app.services.rls_check import check_rls

    _, factory = pg_engine
    async with factory() as session:
        report = await check_rls(session)
    assert not report.superuser, (
        f"роль {report.role} — суперпользователь: RLS её не касается, проверка изоляции бессмысленна"
    )
    assert not report.bypassrls, f"роль {report.role} имеет BYPASSRLS"


async def test_migrations_reach_head_on_postgres(pg_engine) -> None:
    """Ревизии проходят на Postgres — то, ради чего из них убран PRAGMA."""
    from sqlalchemy import text

    engine, _ = pg_engine
    async with engine.connect() as conn:
        version = (await conn.execute(text("select version_num from alembic_version"))).scalar_one()
    assert version >= "0006"


async def test_rls_is_forced_not_merely_enabled(pg_engine) -> None:
    """`ENABLE` без `FORCE` не действует на владельца таблиц.

    Политика была бы создана, видна в `\\d+` и не применялась бы ни к одному
    запросу приложения. Отказ бесшумный — значит проверять обязан тест.
    """
    from app.services.rls_check import check_rls

    _, factory = pg_engine
    async with factory() as session:
        report = await check_rls(session)
    assert report.unforced == [], report.problems()
    assert report.unprotected == [], report.problems()
    assert report.missing_policy == [], report.problems()


async def test_foreign_rows_are_invisible(pg_engine) -> None:
    """Главное: арендатор не видит чужой проект.

    Не «фильтр вернул пусто», а «строка не существует для этой транзакции».
    """
    from sqlalchemy import func, select, text

    from app.models import Project

    _, factory = pg_engine
    alice, bob = str(uuid.uuid4()), str(uuid.uuid4())
    mine, theirs = f"alice-{alice[:8]}", f"bob-{bob[:8]}"

    async with factory() as session:
        for tenant, slug in ((alice, mine), (bob, theirs)):
            await session.execute(text("select set_config('app.tenant_id', :t, false)"), {"t": tenant})
            session.add(Project(slug=slug, topic="тест", tenant_id=tenant))
            await session.commit()

    async with factory() as session:
        await session.execute(text("select set_config('app.tenant_id', :t, false)"), {"t": alice})
        rows = (await session.execute(select(Project.slug))).scalars().all()
        assert rows == [mine]
        total = (await session.execute(select(func.count()).select_from(Project))).scalar_one()
        assert total == 1, "COUNT тоже обязан считать только своё"


async def test_writing_into_foreign_tenant_is_refused(pg_engine) -> None:
    """`WITH CHECK`: строку, которую нельзя прочитать, нельзя и записать.

    Иначе арендатор пишет в чужое пространство вслепую — данные есть, а
    автор их не видит.
    """
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    from app.models import Project

    _, factory = pg_engine
    alice, bob = str(uuid.uuid4()), str(uuid.uuid4())

    async with factory() as session:
        await session.execute(text("select set_config('app.tenant_id', :t, false)"), {"t": alice})
        session.add(Project(slug=f"sneaky-{bob[:8]}", topic="чужое", tenant_id=bob))
        with pytest.raises(DBAPIError):
            await session.commit()


async def test_owner_mode_sees_only_unowned_rows(pg_engine) -> None:
    """Без настройки видны строки без арендатора — и только они.

    Забытая настройка не должна означать «видно всё»: это превратило бы
    любой недосмотр в утечку.
    """
    from sqlalchemy import select, text

    from app.models import Project

    _, factory = pg_engine
    tenant = str(uuid.uuid4())
    legacy, owned = f"owner-legacy-{tenant[:8]}", f"tenant-film-{tenant[:8]}"

    async with factory() as session:
        session.add(Project(slug=legacy, topic="до SaaS", tenant_id=None))
        await session.commit()
    async with factory() as session:
        await session.execute(text("select set_config('app.tenant_id', :t, false)"), {"t": tenant})
        session.add(Project(slug=owned, topic="клиент", tenant_id=tenant))
        await session.commit()

    async with factory() as session:
        slugs = (await session.execute(select(Project.slug))).scalars().all()
    assert legacy in slugs
    assert owned not in slugs


async def test_ledger_invariant_holds_on_postgres(pg_engine) -> None:
    """Касса считается одинаково на обоих движках."""
    from sqlalchemy import text

    from app.services import credit_ledger as cl
    from app.services.credits import price_micro

    _, factory = pg_engine
    tenant = str(uuid.uuid4())
    async with factory() as session:
        await session.execute(text("select set_config('app.tenant_id', :t, false)"), {"t": tenant})
        # Пополнение обязано покрывать холд: `price_micro(5.0)` — это цена
        # с маржой ×3, то есть 15 кредитов, а не 5.
        await cl.topup(session, tenant, 20 * 10**6)
        hold = await cl.open_hold(
            session, tenant, project_id=1, step_code="video", amount_micro=price_micro(5.0)
        )
        await cl.settle_hold(session, hold.id, cost_usd=4.56)
        cached, computed = await cl.reconcile(session, tenant)
        assert cached == computed
        await session.commit()
