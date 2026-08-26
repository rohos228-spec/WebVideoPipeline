"""Стражи знают статусы звуков — иначе вечный цикл на финале.

Звуки добавили в конвейер позже стражей. `sfx_plan` делал 8 событий и ставил
`sfx_plan_ready`; `compute_actual_status` возвращал `music_ready`, страж
откатывал, авто-продвижение одобряло — каждые пять секунд, без конца. Живой
прогон финала 2026-08-26, третий случай одного и того же класса (предметы,
музыка, звуки).

План подтверждается `load_sfx_plan` (чекпоинт в meta или `sfx_plan.json`),
звуки — файлами в `data_dir/sfx/`; при `SFX_ENABLED=false` — самим статусом.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base, Project, ProjectStatus
from app.services import project_state
from app.services.step_data_guard import ready_status_confirmed_by_data


@pytest_asyncio.fixture
async def db(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 's.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    @asynccontextmanager
    async def scope():
        async with factory() as s:
            yield s
            await s.commit()

    yield scope
    await engine.dispose()


async def _project(db, tmp_path, monkeypatch, status):
    async with db() as s:
        p = Project(id=1, slug="rolik", topic="тема", status=status, meta={})
        s.add(p)
        await s.flush()
        data_dir = tmp_path / "videos" / "rolik"
        data_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(type(p), "data_dir", property(lambda self: data_dir), raising=False)
        return s, p, data_dir


@pytest.mark.asyncio
async def test_plan_ready_is_confirmed_by_the_plan(db, tmp_path, monkeypatch):
    from app.settings import settings

    monkeypatch.setattr(settings, "sfx_enabled", True)
    monkeypatch.setattr("app.services.sfx_plan.load_sfx_plan", lambda project: [object()] * 8)
    s, p, _ = await _project(db, tmp_path, monkeypatch, ProjectStatus.sfx_plan_ready)
    assert await ready_status_confirmed_by_data(s, p, ProjectStatus.sfx_plan_ready) is True
    assert project_state._sfx_status(p) is ProjectStatus.sfx_plan_ready


@pytest.mark.asyncio
async def test_sfx_ready_needs_files_not_just_a_plan(db, tmp_path, monkeypatch):
    from app.settings import settings

    monkeypatch.setattr(settings, "sfx_enabled", True)
    monkeypatch.setattr("app.services.sfx_plan.load_sfx_plan", lambda project: [object()])
    s, p, data_dir = await _project(db, tmp_path, monkeypatch, ProjectStatus.sfx_ready)
    assert project_state._sfx_status(p) is ProjectStatus.sfx_plan_ready, "без файлов — только план"

    (data_dir / "sfx").mkdir()
    (data_dir / "sfx" / "sfx_01_door.wav").write_bytes(b"RIFF")
    assert project_state._sfx_status(p) is ProjectStatus.sfx_ready
    assert await ready_status_confirmed_by_data(s, p, ProjectStatus.sfx_ready) is True


@pytest.mark.asyncio
async def test_disabled_sfx_trusts_the_pass_through_status(db, tmp_path, monkeypatch):
    from app.settings import settings

    monkeypatch.setattr(settings, "sfx_enabled", False)
    s, p, _ = await _project(db, tmp_path, monkeypatch, ProjectStatus.sfx_ready)
    assert await ready_status_confirmed_by_data(s, p, ProjectStatus.sfx_ready) is True


@pytest.mark.asyncio
async def test_without_a_plan_the_status_is_not_confirmed(db, tmp_path, monkeypatch):
    """Обратная сторона: подтверждение — по данным, а не «всегда да»."""
    from app.settings import settings

    monkeypatch.setattr(settings, "sfx_enabled", True)
    monkeypatch.setattr("app.services.sfx_plan.load_sfx_plan", lambda project: None)
    s, p, _ = await _project(db, tmp_path, monkeypatch, ProjectStatus.sfx_plan_ready)
    assert project_state._sfx_status(p) is None
    assert await ready_status_confirmed_by_data(s, p, ProjectStatus.sfx_plan_ready) is False
