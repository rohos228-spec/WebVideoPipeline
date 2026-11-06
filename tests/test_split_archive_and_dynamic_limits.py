"""Тесты фильтрации архивных кадров и динамических лимитов разбивки."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select

from app.models import Frame, Project
from app.services import db_v2
from app.services.node_step_params import (
    build_split_db_hint,
    calculate_fallback_split_limits,
    resolve_max_split_frames,
)
from app.web.routers.frames import list_frames


def test_resolve_max_split_frames_shorts() -> None:
    """Для короткого текста без кастомных настроек крышка 30 кадров."""
    p = Project(topic="short")
    p.meta = {}
    p.script_text = "Короткий закадр на 500 символов." * 2
    max_f = resolve_max_split_frames(p, p.script_text)
    assert max_f == 30


def test_resolve_max_split_frames_long_text() -> None:
    """Для длинного текста (9000 знаков) крышка не должна быть 30 кадров."""
    p = Project(topic="long")
    p.meta = {}
    vo = "Длинный закадровый текст про расследование дела. " * 180  # ~8800 знаков
    max_f = resolve_max_split_frames(p, vo)
    assert max_f > 75


def test_resolve_max_split_frames_respects_cell_max() -> None:
    """Если cell_max_chars = 120, то 9000 знаков требуют минимум 75 кадров."""
    p = Project(topic="limits")
    p.meta = {
        "node_step_params": {
            "split": {
                "cell_min_chars": 30,
                "cell_max_chars": 120,
                "cell_avg_min": 50,
                "cell_avg_max": 90,
            }
        }
    }
    vo = "А" * 9000
    max_f = resolve_max_split_frames(p, vo)
    assert max_f >= 75


def test_resolve_max_split_frames_respects_requested() -> None:
    """Если запрошено 45 кадров, max_frames >= 45."""
    p = Project(topic="plan")
    p.meta = {
        "node_step_params": {
            "split": {
                "frames_count": 45,
            }
        }
    }
    max_f = resolve_max_split_frames(p, "А" * 2000)
    assert max_f >= 45


def test_build_split_db_hint_shorts() -> None:
    """Для короткого текста подсказка содержит формулировку Shorts."""
    p = Project(topic="short")
    p.meta = {}
    hint = build_split_db_hint(p, "Короткий текст")
    assert "ФОРМАТ SHORTS" in hint
    assert "≤30 кадров" in hint


def test_build_split_db_hint_custom_limits() -> None:
    """При явных лимитах ячеек подсказка требует строго их соблюдать и не ограничивает 30 кадрами."""
    p = Project(topic="custom")
    p.meta = {
        "node_step_params": {
            "split": {
                "cell_min_chars": 30,
                "cell_max_chars": 120,
                "cell_avg_min": 50,
                "cell_avg_max": 90,
            }
        }
    }
    hint = build_split_db_hint(p, "А" * 9000)
    assert "СТРОГИЕ НАСТРОЙКИ РАЗБИВКИ ЯЧЕЕК" in hint
    assert "минимум 30" in hint
    assert "максимум 120" in hint
    assert "ФОРМАТ SHORTS" not in hint
    assert "≤30 кадров" not in hint


def test_build_split_db_hint_long_without_custom_limits() -> None:
    """Для длинного текста подсказка не навязывает лимит 30 кадров."""
    p = Project(topic="long")
    p.meta = {}
    hint = build_split_db_hint(p, "А" * 8000)
    assert "Длинный ролик" in hint
    assert "≤30 кадров" not in hint


def test_calculate_fallback_split_limits_long_text() -> None:
    """При 9000 знаков целевое число кадров не должно резаться до 24-30."""
    p = Project(topic="long")
    p.meta = {}
    p.script_text = "А" * 9000
    calc_min, calc_max, calc_avg_min, calc_avg_max = calculate_fallback_split_limits(p)
    # Средняя длина кадра при 9000 зн должна быть порядка 50-100, а не 300+
    assert calc_avg_min < 100
    assert calc_avg_max < 150
    assert calc_max < 250


@pytest.mark.asyncio
async def test_replace_all_frames_and_list_frames_archived_filtering(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Архивация старых кадров и их фильтрация в list_frames и project_graph."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.models import Base
    from app.settings import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 't.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as session:
        p = Project(topic="t", slug="p_arch")
        session.add(p)
        await session.flush()

        specs_1 = [{"закадр": f"Кадр 1.{i}"} for i in range(1, 4)]
        frames_1 = await db_v2.replace_all_frames(session, p, specs_1)
        assert len(frames_1) == 3
        assert [f.number for f in frames_1] == [1, 2, 3]

        # Первый вызов list_frames — 3 кадра
        res_1 = await list_frames(p.id, session=session)
        assert len(res_1) == 3
        assert [f.number for f in res_1] == [1, 2, 3]

        # Второй replace — старые кадры уходят в архив
        specs_2 = [{"закадр": f"Кадр 2.{i}"} for i in range(1, 5)]
        frames_2 = await db_v2.replace_all_frames(session, p, specs_2)
        assert len(frames_2) == 4
        assert [f.number for f in frames_2] == [1, 2, 3, 4]

        # В базе суммарно 7 кадров (3 архивных + 4 активных)
        all_in_db = list((await session.execute(select(Frame).where(Frame.project_id == p.id))).scalars())
        assert len(all_in_db) == 7

        # list_frames возвращает ТОЛЬКО 4 активных кадра (не 7!)
        active_res = await list_frames(p.id, session=session)
        assert len(active_res) == 4
        assert [f.number for f in active_res] == [1, 2, 3, 4]

        # project_graph также возвращает только 4 активных кадра
        graph = await db_v2.project_graph(session, p)
        assert len(graph["frames"]) == 4
        assert [f["number"] for f in graph["frames"]] == [1, 2, 3, 4]

        # Третий replace — только 4 кадра архивируются, архивные не плодятся повторно
        specs_3 = [{"закадр": f"Кадр 3.{i}"} for i in range(1, 3)]
        frames_3 = await db_v2.replace_all_frames(session, p, specs_3)
        assert len(frames_3) == 2

        active_res_3 = await list_frames(p.id, session=session)
        assert len(active_res_3) == 2
        assert [f.number for f in active_res_3] == [1, 2]
