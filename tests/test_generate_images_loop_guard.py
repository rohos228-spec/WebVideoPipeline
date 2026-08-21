"""Tests verifying generate_images does not hang in infinite sleep loops."""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base, Frame, FrameStatus, Project, ProjectStatus
from app.orchestrator.steps.generate_images import (
    INFLIGHT_ATTR,
    _claim_shot1_batch,
    _clear_stale_inflight,
)


@pytest_asyncio.fixture
async def db_session(tmp_path):
    db_file = tmp_path / "test_images_loop.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest.mark.asyncio
async def test_clear_stale_inflight_frees_stuck_frames(db_session, tmp_path) -> None:
    """Ensure stuck INFLIGHT_ATTR from interrupted runs is cleared cleanly."""
    project = Project(
        topic="Тест генерации",
        slug="test-images-gen",
        status=ProjectStatus.generating_images,
        meta={},
    )
    db_session.add(project)
    await db_session.commit()
    await db_session.refresh(project)

    # Frame with stuck INFLIGHT_ATTR
    f = Frame(
        project_id=project.id,
        number=1,
        voiceover_text="Кадр 1",
        image_prompt="A realistic futuristic robot, 8k",
        status=FrameStatus.image_prompt_ready,
        attrs={INFLIGHT_ATTR: True},
    )
    db_session.add(f)
    await db_session.commit()

    scenes_dir = tmp_path / "scenes"
    scenes_dir.mkdir(parents=True, exist_ok=True)

    # Этап 2 (D.3): legacy-маркер INFLIGHT_ATTR больше не блокирует claim —
    # двойное исполнение отсекает lease в _generate_frame_job. Застрявший
    # маркер прерванного прогона не «замораживает» кадр навсегда.
    claimed = await _claim_shot1_batch(db_session, project.id, scenes_dir, limit=1)
    assert len(claimed) == 1
    assert claimed[0].number == 1

    # Чистка legacy-маркеров по-прежнему работает (одноразовая гигиена).
    cleared_count = await _clear_stale_inflight(db_session, project.id)
    await db_session.commit()
    assert cleared_count == 1