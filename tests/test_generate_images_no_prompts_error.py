"""Пустая очередь картинок объясняет оператору, что делать.

Шаг падает, когда в БД нет ни одного `image_prompt`, и текст ошибки — половина
пользы: оператор читает его в Telegram и идёт делать названный шаг. Раньше он
отправлял в «Импорт Excel» — кнопки с таким именем в интерфейсе нет, книга из
текстового контура убрана. Теперь называет `img_pr`.

Тест держит именно текст. Логика очереди проверяется в
`test_generate_images_queue.py`, здесь — контракт сообщения.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base, Frame, FrameStatus, Project, ProjectStatus
from app.orchestrator.steps.generate_images import run


@pytest.fixture
async def session(tmp_path: Path) -> AsyncSession:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'img.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


async def _project_with_empty_prompts(
    session: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Project:
    data_root = tmp_path / "data"
    data_root.mkdir()
    monkeypatch.setattr("app.settings.settings.data_dir", str(data_root))
    project = Project(id=11, slug="img", topic="Вулканы", status=ProjectStatus.generating_images)
    session.add(project)
    session.add_all(
        Frame(project_id=11, number=n, voiceover_text="x", image_prompt="", status="planned") for n in (1, 2)
    )
    await session.flush()
    (project.data_dir / "scenes").mkdir(parents=True)
    return project


@pytest.mark.asyncio
async def test_error_names_the_step_that_fills_prompts(
    tmp_path: Path, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = await _project_with_empty_prompts(session, tmp_path, monkeypatch)

    with pytest.raises(RuntimeError) as err:
        await run(session, project, bot=None)  # type: ignore[arg-type]

    text = str(err.value)
    assert "нет image_prompt" in text
    assert "img_pr" in text
    assert "Excel" not in text


@pytest.mark.asyncio
async def test_frames_without_prompt_are_marked_failed(
    tmp_path: Path, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Побочный контракт: кадры без промта помечены причиной, а не просто брошены."""
    project = await _project_with_empty_prompts(session, tmp_path, monkeypatch)

    with pytest.raises(RuntimeError):
        await run(session, project, bot=None)  # type: ignore[arg-type]

    frames = (await session.execute(select(Frame))).scalars().all()
    assert {f.status for f in frames} == {FrameStatus.failed}
    assert all((f.attrs or {}).get("fail_reason") == "no_image_prompt" for f in frames)


@pytest.mark.asyncio
async def test_step_is_a_noop_in_another_status(
    tmp_path: Path, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = await _project_with_empty_prompts(session, tmp_path, monkeypatch)
    project.status = ProjectStatus.plan_ready
    await run(session, project, bot=None)  # type: ignore[arg-type]
