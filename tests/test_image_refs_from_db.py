"""ID референсов кадра берутся из БД, а xlsx остаётся запасным путём.

Контракт (docs/PROMPT_CONTRACT.md) — apply-ops в БД; лист xlsx помечен
deprecated и после scene_design обычно пуст. Пока рефы читались только из
листа, генератор получал `refs=0` и рисовал новое лицо в каждом кадре.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base, Frame, Project, ProjectStatus
from app.orchestrator.steps.generate_images import _ref_ids_from_db


@pytest_asyncio.fixture
async def db(tmp_path, monkeypatch):
    from app.settings import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    (tmp_path / "videos" / "refs-test").mkdir(parents=True)
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'refs.db'}", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        project = Project(topic="t", slug="refs-test", status=ProjectStatus.generating_images)
        session.add(project)
        await session.flush()
        yield session, project
    await engine.dispose()


@pytest.mark.asyncio
async def test_ids_come_from_frame_attrs(db) -> None:
    session, project = db
    session.add(
        Frame(
            project_id=project.id,
            number=7,
            voiceover_text="",
            attrs={"characters": "c01, c02", "shot01_props": "predmet1"},
        )
    )
    await session.flush()

    persons, items = await _ref_ids_from_db(session, project, 7)
    assert persons == ["c01", "c02"]
    assert items == ["predmet1"]


@pytest.mark.asyncio
async def test_frame_without_people_returns_nothing(db) -> None:
    """Пустой кадр не должен подсовывать чужие рефы — лучше без референса."""
    session, project = db
    session.add(Frame(project_id=project.id, number=1, voiceover_text="", attrs={}))
    await session.flush()

    assert await _ref_ids_from_db(session, project, 1) == ([], [])


@pytest.mark.asyncio
async def test_missing_frame_is_not_an_error(db) -> None:
    session, project = db
    assert await _ref_ids_from_db(session, project, 99) == ([], [])
