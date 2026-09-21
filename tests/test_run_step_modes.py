"""Режимы запуска шагов: обычный ▶ не жжёт готовое и включает auto_mode."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import (
    Artifact,
    ArtifactKind,
    Base,
    Frame,
    FrameStatus,
    Project,
    ProjectStatus,
)
from app.services.project_steps import start_step


@pytest.fixture
async def session(tmp_path: Path):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 't_modes.db'}"
    engine = create_async_engine(db_url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


@pytest.mark.asyncio
async def test_start_step_ui_play_without_force_wipe_keeps_images(session, tmp_path: Path):
    """Обычный ▶ не жжёт готовые картинки и включает auto_mode для цепочки."""
    data_dir = tmp_path / "p3"
    scenes_dir = data_dir / "scenes"
    scenes_dir.mkdir(parents=True, exist_ok=True)
    img_file = scenes_dir / "scene_001.png"
    img_file.write_bytes(b"dummy png data")

    p = Project(
        slug="p3",
        topic="Topic",
        status=ProjectStatus.images_ready,
        auto_mode=False,
    )
    session.add(p)
    await session.flush()

    fr = Frame(
        project_id=p.id,
        number=1,
        voiceover_text="Voiceover for frame 1",
        image_prompt="A futuristic city",
        status=FrameStatus.image_generated,
    )
    session.add(fr)
    await session.flush()

    art = Artifact(
        project_id=p.id,
        frame_id=fr.id,
        uuid="art_uuid_play",
        kind=ArtifactKind.scene_image,
        path=str(img_file),
    )
    session.add(art)
    await session.commit()

    await start_step(session, p, "img", explicit_ui_start=True)
    await session.commit()

    remaining_arts = (
        (
            await session.execute(
                select(Artifact).where(
                    Artifact.project_id == p.id,
                    Artifact.kind == ArtifactKind.scene_image,
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(remaining_arts) == 1
    assert img_file.exists()
    # Тумблер «Автозапуск» ручной ▶ не включает — только сам тумблер.
    assert p.auto_mode is False
