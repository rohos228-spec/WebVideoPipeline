"""`MUSIC_ENABLED=false`: шаг проходит вхолостую, и стражи это принимают.

Музыка (Suno через Outsee) идёт только через браузер. На сервере Chrome нет —
шаг упирался бы в CDP так же, как герои до правки, и финальную стадию нельзя
было бы пройти вовсе. Сборка подмешивает музыку только если файл есть, так
что без неё ролик собирается.

Одного выключателя мало — нужна метка. Без `meta.music_skipped`
`compute_actual_status` не подтвердил бы `music_ready` (артефакта нет),
страж откатил бы статус, авто-продвижение вернуло — вечный цикл, ровно тот,
что уже ловили на предметах. Поэтому метку уважают все трое: расчёт статуса,
страж и пост-проверка.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base, Project, ProjectStatus


@pytest_asyncio.fixture
async def db(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'm.db'}")
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


@pytest.mark.asyncio
async def test_step_passes_through_with_a_marker(db, tmp_path, monkeypatch):
    from app.orchestrator.steps import generate_music
    from app.settings import settings

    monkeypatch.setattr(settings, "music_enabled", False)

    def _boom():
        raise AssertionError("шаг полез в браузер при выключенной музыке")

    monkeypatch.setattr(generate_music, "browser_session", _boom)

    async with db() as s:
        p = Project(id=1, slug="rolik", topic="тема", status=ProjectStatus.generating_music, meta={})
        s.add(p)
        await s.flush()
        monkeypatch.setattr(type(p), "data_dir", property(lambda self: tmp_path), raising=False)
        await generate_music.run(s, p, None)
        assert p.status is ProjectStatus.music_ready
        assert p.meta.get("music_skipped") is True


@pytest.mark.asyncio
async def test_validator_accepts_the_marker_and_rejects_without_it(db, tmp_path):
    from app.services.post_step_validate import validate_after_music

    async with db() as s:
        p = Project(
            id=2, slug="r2", topic="т", status=ProjectStatus.music_ready, meta={"music_skipped": True}
        )
        s.add(p)
        await s.flush()
        assert (await validate_after_music(s, p)).ok

        q = Project(id=3, slug="r3", topic="т", status=ProjectStatus.music_ready, meta={})
        s.add(q)
        await s.flush()
        # Без метки и без файла — прежний отказ: выключатель не выключил проверку.
        assert not (await validate_after_music(s, q)).ok


def test_actual_status_helper_reads_the_marker():
    from app.services.project_state import _music_skipped

    assert _music_skipped(SimpleNamespace(meta={"music_skipped": True})) is True
    assert _music_skipped(SimpleNamespace(meta={})) is False
