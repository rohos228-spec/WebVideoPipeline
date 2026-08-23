"""Расстановка кадра доезжает до промта картинки.

Живой прогон #2: ``attrs["continuity"]`` посчитан на 23 кадрах из 24, а в
промт картинки попал ровно на одном. Поле приехало в контекст, но в списке
полей тела промта его не было, и агент молча прошёл мимо. Контракт починен;
эти проверки держат вторую линию — код переносит расстановку сам, если агент
опять её не перенёс.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base, Frame, Project, ProjectStatus
from app.services.img_pr_continuity import (
    ensure_continuity_line,
    has_continuity_line,
)

_CONT = (
    "Непрерывность. Расстановка: Игнат в левой половине кадра, смотрит вправо; "
    "женщина в сером пальто в правой половине кадра, смотрит влево. "
    "жёлтый конверт держит Игнат."
)

_PROMPT = "\n".join(
    [
        "Референс: c01 (Игнат), c02 (женщина в сером пальто).",
        "Место: последний вагон ночного поезда.",
        "Фон: тёмное окно, ряд спинок уходит в глубину.",
        "План-ракурс: средний план, eye-level.",
        "Действие: Игнат подаётся вперёд, конверт уже вышел из-под локтя.",
        "Смысл: обмен начался.",
        "",
        "STYLE: cinematic documentary photography",
        "Negative: 3D render, twins, clones",
    ]
)


def test_line_lands_between_background_and_action() -> None:
    """Геометрия стоит выше сюжета: у MiniMax промт режется по 1500 с хвоста."""
    out = ensure_continuity_line(_PROMPT, _CONT).split("\n")
    assert out[out.index("Фон: тёмное окно, ряд спинок уходит в глубину.") + 1] == _CONT
    assert out.index(_CONT) < next(i for i, ln in enumerate(out) if ln.startswith("Действие:"))


def test_second_pass_changes_nothing() -> None:
    once = ensure_continuity_line(_PROMPT, _CONT)
    assert ensure_continuity_line(once, _CONT) == once


def test_model_copy_with_own_line_breaks_counts_as_done() -> None:
    """Модель перенесла строку, но переставила переносы — это не повод дублировать."""
    reflowed = _PROMPT.replace(
        "План-ракурс:",
        "Непрерывность. Расстановка: Игнат в левой половине кадра,\n"
        "смотрит вправо; женщина в сером пальто в правой половине кадра,\n"
        "смотрит влево. жёлтый конверт держит Игнат.\nПлан-ракурс:",
    )
    assert has_continuity_line(reflowed, _CONT)
    assert ensure_continuity_line(reflowed, _CONT) == reflowed


def test_no_continuity_field_means_no_invention() -> None:
    assert ensure_continuity_line(_PROMPT, "") == _PROMPT
    assert ensure_continuity_line(_PROMPT, None) == _PROMPT


def test_prompt_without_background_still_gets_the_line() -> None:
    body = "Место: вагон.\nДействие: Игнат встаёт.\nSTYLE: cinematic"
    out = ensure_continuity_line(body, _CONT).split("\n")
    assert out.index(_CONT) < out.index("Действие: Игнат встаёт.")


@pytest_asyncio.fixture
async def img_pr_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from app.settings import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    (tmp_path / "videos" / "img-pr-cont").mkdir(parents=True)

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'ipc.db'}", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        project = Project(
            topic="t",
            slug="img-pr-cont",
            status=ProjectStatus.generating_image_prompts,
            script_text="раз два",
        )
        session.add(project)
        await session.flush()
        yield session, project
    await engine.dispose()


@pytest.mark.asyncio
async def test_step_repairs_frames_the_agent_skipped(img_pr_session) -> None:
    """Промт без расстановки чинится; промт с расстановкой не трогается."""
    from app.services.img_pr_continuity import enforce_continuity_in_prompts

    session, project = img_pr_session
    session.add_all(
        [
            Frame(
                project_id=project.id,
                number=1,
                uuid="a" * 32,
                voiceover_text="раз",
                image_prompt=_PROMPT,
                attrs={"continuity": _CONT},
            ),
            Frame(
                project_id=project.id,
                number=2,
                uuid="b" * 32,
                voiceover_text="два",
                image_prompt=ensure_continuity_line(_PROMPT, _CONT),
                attrs={"continuity": _CONT},
            ),
            # Кадр без расстановки: у одиночных сцен оси нет, и это не брак.
            Frame(
                project_id=project.id,
                number=3,
                uuid="c" * 32,
                voiceover_text="три",
                image_prompt=_PROMPT,
                attrs={},
            ),
        ]
    )
    await session.flush()

    stats = await enforce_continuity_in_prompts(session, project)
    assert stats == {"frames": 3, "with_continuity": 2, "from_model": 1, "repaired": 1}

    rows = (
        (await session.execute(select(Frame).where(Frame.project_id == project.id).order_by(Frame.number)))
        .scalars()
        .all()
    )
    assert has_continuity_line(rows[0].image_prompt, _CONT)
    assert has_continuity_line(rows[1].image_prompt, _CONT)
    assert rows[2].image_prompt == _PROMPT
