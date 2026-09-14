"""Самовосстановление дефолтного Workflow в run_sync._get_default_workflow_id."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.db
from app.models import Base, Workflow
from app.services.run_sync import _get_default_workflow_id
from app.settings import settings


@pytest.fixture
async def master_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_file = tmp_path / "state.db"
    monkeypatch.setattr(settings, "sqlite_path", db_file)
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(app.db, "SessionLocal", factory)
    monkeypatch.setattr(app.db, "engine", engine)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest.mark.asyncio
async def test_get_default_workflow_id_existing(master_db) -> None:
    session = master_db
    wf = Workflow(name="Custom Workflow", is_default=True, nodes=[], edges=[])
    session.add(wf)
    await session.commit()
    await session.refresh(wf)

    assert await _get_default_workflow_id(session) == wf.id


@pytest.mark.asyncio
async def test_get_default_workflow_id_fallback_when_none_marked_default(
    master_db,
) -> None:
    session = master_db
    wf = Workflow(name="Unmarked Workflow", is_default=False, nodes=[], edges=[])
    session.add(wf)
    await session.commit()
    await session.refresh(wf)

    assert await _get_default_workflow_id(session) == wf.id
    # Починился: пометил единственный воркфлоу дефолтным
    await session.refresh(wf)
    assert wf.is_default is True


@pytest.mark.asyncio
async def test_get_default_workflow_id_auto_seeds_when_empty(master_db) -> None:
    session = master_db
    wf_id = await _get_default_workflow_id(session)
    assert wf_id is not None
    assert wf_id > 0

    wf = (await session.execute(select(Workflow).where(Workflow.id == wf_id))).scalar_one_or_none()
    assert wf is not None
    assert wf.is_default is True
