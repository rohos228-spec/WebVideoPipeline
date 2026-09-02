"""Запись реестра персонажей шумит, когда под одним кодом несколько тел.

Живой прогон 2026-08-31: сцен-агент положил в c02 «трое взрослых круглых
существ». Дальше кадру достаётся одна фотография-референс, а промт просит
нарисовать нескольких — выходят клоны. Кодом это не проверялось нигде.

Предупреждение, а не отказ: карточки пишет модель, и ронять запись на живом
проекте нельзя — человек ещё может поправить формулировку.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base, Project, ProjectStatus
from app.services.db_apply import apply_ops


@pytest.fixture
async def mem_db(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    from app import settings as app_settings

    monkeypatch.setattr(app_settings.settings, "data_dir", tmp_path / "data")
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    @asynccontextmanager
    async def _scope():
        async with factory() as session:
            yield session

    yield _scope
    await engine.dispose()


async def _project(session) -> Project:
    p = Project(slug=f"p-{uuid.uuid4().hex[:8]}", topic="т", status=ProjectStatus.frames_ready, meta={})
    session.add(p)
    await session.flush()
    return p


@pytest.mark.asyncio
async def test_plural_card_is_flagged_but_written(mem_db) -> None:
    async with mem_db() as session:
        p = await _project(session)
        res = await apply_ops(
            session,
            p,
            [],
            characters=[{"id": "c02", "внешность": "трое взрослых круглых существ"}],
        )
        assert res.get("characters") == 1  # запись не отменена
        warnings = (p.meta or {}).get("cast_invariant_warnings") or []
        assert any("c02" in w for w in warnings)


@pytest.mark.asyncio
async def test_clean_registry_leaves_no_warnings(mem_db) -> None:
    async with mem_db() as session:
        p = await _project(session)
        await apply_ops(
            session,
            p,
            [],
            characters=[
                {"id": "c01", "внешность": "человек около тридцати", "одежда": "ветровка"},
                {"id": "c02", "внешность": "синий круглый шар", "одежда": "жёлтая сумка"},
            ],
        )
        assert not (p.meta or {}).get("cast_invariant_warnings")


@pytest.mark.asyncio
async def test_broken_checker_does_not_cancel_the_write(mem_db, monkeypatch) -> None:
    """Исключение самой проверки — в лог, запись реестра не отменяется."""

    def _boom(_cards):
        raise RuntimeError("чекер сломан")

    monkeypatch.setattr("app.services.cast_invariants.check_cast_cards", _boom)
    async with mem_db() as session:
        p = await _project(session)
        res = await apply_ops(
            session,
            p,
            [],
            characters=[{"id": "c01", "внешность": "высокий лесник"}],
        )
        assert res.get("characters") == 1
        assert "cast_invariant_warnings" not in (p.meta or {})
