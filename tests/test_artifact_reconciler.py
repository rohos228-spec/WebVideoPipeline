"""Тесты для сервиса сверки и восстановления артефактов (artifact_reconciler)."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import (
    Artifact,
    ArtifactKind,
    Base,
    Frame,
    FrameStatus,
    MediaCall,
    Project,
    ProjectStatus,
    StudioUser,
)
from app.services.artifact_reconciler import parse_frame_file, reconcile_all


@pytest.fixture
async def async_session() -> AsyncSession:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session

    await engine.dispose()


def test_parse_frame_file() -> None:
    assert parse_frame_file("frame_001_s1_randomhash.png") == (1, 1)
    assert parse_frame_file("frame_005_s2_randomhash.webp") == (5, 2)
    assert parse_frame_file("frame_012_something.jpg") == (12, 1)
    assert parse_frame_file("clip_003_s2_hash.mp4") == (3, 2)
    assert parse_frame_file("clip_007_hash.mp4") == (7, 1)
    assert parse_frame_file("unrelated_file.txt") is None


@pytest.mark.asyncio
async def test_reconcile_creates_missing_artifacts_and_repairs_tenants(
    async_session: AsyncSession, tmp_path: Path
) -> None:
    session = async_session

    tenant_id = str(uuid.uuid4())
    user = StudioUser(id=tenant_id, email="owner@test.com", display_name="Owner")
    session.add(user)

    # Создаём структуру директорий проекта
    proj_dir = tmp_path / "videos" / "test-proj"
    scenes_dir = proj_dir / "scenes"
    videos_dir = proj_dir / "videos"
    scenes_dir.mkdir(parents=True)
    videos_dir.mkdir(parents=True)

    # Создаём тестовые файлы на диске
    img_file = scenes_dir / "frame_001_s1_abc.png"
    img_file.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

    vid_file = videos_dir / "clip_001_s1_abc.mp4"
    vid_file.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 100)

    # Создаём проект в БД
    project = Project(
        title="Test Project",
        slug="test-proj",
        topic="Testing Topic",
        status=ProjectStatus.new,
        tenant_id=tenant_id,
        meta={"data_dir": str(proj_dir)},
    )
    session.add(project)
    await session.flush()

    # Кадр 1 со статусом planned
    frame = Frame(
        project_id=project.id,
        number=1,
        voiceover_text="Sample voiceover text",
        status=FrameStatus.planned,
        image_prompt="A test prompt",
    )
    session.add(frame)

    # Непривязанный MediaCall (tenant_id = None)
    mc = MediaCall(
        tenant_id=None,
        project_id=project.id,
        provider="kie",
        kind="image",
        model="flux-2-pro",
        units=1.0,
        cost_usd=0.05,
        result="ok",
    )
    session.add(mc)
    await session.commit()

    # Запускаем сверку
    stats = await reconcile_all(
        session,
        project_id=project.id,
        generate_thumbs=False,
        dry_run=False,
    )

    # Проверяем статистику
    assert stats.projects_scanned == 1
    assert stats.artifacts_found_on_disk == 2
    assert stats.artifacts_created == 2
    assert stats.frames_status_updated == 2
    assert stats.media_calls_repaired == 1

    # Проверяем состояние в БД
    arts = (
        (await session.execute(select(Artifact).where(Artifact.project_id == project.id)))
        .scalars()
        .all()
    )
    assert len(arts) == 2

    img_art = next(a for a in arts if a.kind == ArtifactKind.scene_image)
    assert img_art.tenant_id == tenant_id
    assert img_art.frame_id == frame.id
    assert img_art.meta.get("shot") == 1
    assert img_art.meta.get("recovered") is True

    vid_art = next(a for a in arts if a.kind == ArtifactKind.scene_video)
    assert vid_art.tenant_id == tenant_id
    assert vid_art.frame_id == frame.id

    # Кадр обновился
    await session.refresh(frame)
    assert frame.status == FrameStatus.video_generated

    # MediaCall починился
    await session.refresh(mc)
    assert mc.tenant_id == tenant_id

    # Повторный запуск идемпотентен: ничего нового не создаётся
    stats2 = await reconcile_all(
        session,
        project_id=project.id,
        generate_thumbs=False,
        dry_run=False,
    )
    assert stats2.artifacts_created == 0
    assert stats2.artifacts_already_in_db == 2
