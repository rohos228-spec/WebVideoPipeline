"""Непрерывность доезжает до кадров и до промта картинки.

Проверяем не арифметику (она в ``test_scene_continuity``), а сборку входа:
ось берётся из персонажей кадров, предметы — из карточек скелета через
VO-родителя, а результат ложится в ``attrs["continuity"]`` и попадает в
белый список полей, которые видит img_pr.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base, Frame, Project, ProjectStatus

_VO = "Игнат едет один. Входит женщина. Конверт переходит из рук в руки."


@pytest_asyncio.fixture
async def cont_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from app.settings import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    (tmp_path / "videos" / "cont-test").mkdir(parents=True)

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'cont.db'}", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        project = Project(
            topic="t",
            slug="cont-test",
            status=ProjectStatus.scene_design_ready,
            script_text=_VO,
            meta={"scene_design_enabled": True},
        )
        session.add(project)
        await session.flush()
        yield session, project
    await engine.dispose()


def _add_frame(
    session,
    project: Project,
    number: int,
    *,
    uuid: str,
    parent: str,
    scene: str,
    who: str,
    action: str,
) -> None:
    session.add(
        Frame(
            project_id=project.id,
            number=number,
            uuid=uuid,
            sort_key=float(number),
            voiceover_text="" if parent != uuid else _VO,
            attrs={
                "id_scene": scene,
                "characters": who,
                "shot01_action": action,
                "camera_subdivide": {"parent_uuid": parent, "shot_index": 1},
            },
        )
    )


@pytest.mark.asyncio
async def test_continuity_lands_on_frames_from_scene_and_skeleton(cont_session) -> None:
    session, project = cont_session
    from app.services.scene_design import runner
    from app.services.scene_design.continuity_apply import apply_continuity

    _add_frame(session, project, 1, uuid="u1", parent="u1", scene="sc01", who="c01", action="достаёт конверт")
    _add_frame(
        session,
        project,
        2,
        uuid="u2",
        parent="u2",
        scene="sc02",
        who="c01, c02",
        action="садится напротив",
    )
    _add_frame(
        session,
        project,
        3,
        uuid="u3",
        parent="u2",
        scene="sc02",
        who="c01, c02",
        action="смотрят друг на друга",
    )
    await session.flush()

    runner.save_checkpoint(
        project,
        "characters",
        {
            "characters": [
                {"id": "c01", "имя": "Игнат"},
                {"id": "c02", "имя": "женщина в сером пальто"},
            ]
        },
    )
    runner.save_checkpoint(
        project,
        "skeleton",
        {
            "items_seed": [{"id": "p01", "имя": "жёлтый конверт"}],
            "scenes": [
                {"кадр": 1, "предметы": [{"id": "p01", "как": "вводится", "у_кого": "c01"}]},
                {"кадр": 2, "предметы": [{"id": "p01", "как": "держится", "у_кого": "c01"}]},
            ],
        },
    )

    report = await apply_continuity(session, project)
    assert report["frames"] == 3
    assert report["violations"] == []

    frames = (await session.execute(select(Frame).where(Frame.project_id == project.id))).scalars().all()
    by_uuid = {f.uuid: f for f in frames}

    # Сцена из одного человека — оси нет, но предмет назван.
    first = by_uuid["u1"].attrs["continuity"]
    assert "жёлтый конверт держит Игнат" in first
    assert "половине кадра" not in first

    # Двухкадровая сцена: расстановка одна и та же в обоих кадрах.
    assert by_uuid["u2"].attrs["continuity"] == by_uuid["u3"].attrs["continuity"]
    line = by_uuid["u2"].attrs["continuity"]
    assert "Игнат в левой половине кадра, смотрит вправо" in line
    assert "женщина в сером пальто в правой половине кадра, смотрит влево" in line
    # Дочерний кадр наследует предметы своего VO-родителя.
    assert "жёлтый конверт" in by_uuid["u3"].attrs["continuity"]


@pytest.mark.asyncio
async def test_continuity_is_visible_to_image_prompt_step(cont_session) -> None:
    """Строка бесполезна, если её не видит img_pr — держим ключ в whitelist."""
    from app.services.db_frames_context import _IMG_PR_ATTR_KEYS, slim_attrs_for_excel_gpt

    assert "continuity" in _IMG_PR_ATTR_KEYS
    slim = slim_attrs_for_excel_gpt({"continuity": "Непрерывность. Игнат слева.", "place": "вагон"})
    assert slim["continuity"].startswith("Непрерывность.")


@pytest.mark.asyncio
async def test_continuity_reports_prop_teleport(cont_session) -> None:
    session, project = cont_session
    from app.services.scene_design import runner
    from app.services.scene_design.continuity_apply import apply_continuity

    _add_frame(
        session, project, 1, uuid="u1", parent="u1", scene="sc01", who="c01, c02", action="держит конверт"
    )
    _add_frame(
        session,
        project,
        2,
        uuid="u2",
        parent="u2",
        scene="sc01",
        who="c01, c02",
        action="оба молчат и смотрят в окно",
    )
    await session.flush()
    runner.save_checkpoint(project, "characters", {"characters": [{"id": "c01", "имя": "Игнат"}]})
    runner.save_checkpoint(
        project,
        "skeleton",
        {
            "items_seed": [{"id": "p01", "имя": "конверт"}],
            "scenes": [
                {"кадр": 1, "предметы": [{"id": "p01", "как": "вводится", "у_кого": "c01"}]},
                {"кадр": 2, "предметы": [{"id": "p01", "как": "держится", "у_кого": "c02"}]},
            ],
        },
    )

    report = await apply_continuity(session, project)
    assert any("prop_teleport" in v for v in report["violations"])
    frames = (await session.execute(select(Frame).where(Frame.project_id == project.id))).scalars().all()
    second = next(f for f in frames if f.uuid == "u2")
    # Починка, а не только жалоба: конверт остался у прежнего владельца.
    assert "конверт держит Игнат" in second.attrs["continuity"]


@pytest.mark.asyncio
async def test_cell_prop_event_fires_once_per_vo_cell(cont_session) -> None:
    """Камера дробит ячейку на кадры — событие предмета от этого не удваивается."""
    session, project = cont_session
    from app.services.scene_design import runner
    from app.services.scene_design.continuity_apply import apply_continuity

    # Одна VO-ячейка (parent u1) разложена камерой на три кадра.
    for i, uid in enumerate(("u1", "u1b", "u1c"), start=1):
        _add_frame(
            session,
            project,
            i,
            uuid=uid,
            parent="u1",
            scene="sc01",
            who="c01",
            action="достаёт конверт" if i == 1 else "смотрит в окно",
        )
    await session.flush()
    runner.save_checkpoint(project, "characters", {"characters": [{"id": "c01", "имя": "Игнат"}]})
    runner.save_checkpoint(
        project,
        "skeleton",
        {
            "items_seed": [{"id": "p01", "имя": "конверт"}],
            "scenes": [{"кадр": 1, "предметы": [{"id": "p01", "как": "вводится"}]}],
        },
    )

    report = await apply_continuity(session, project)
    assert report["violations"] == []
    frames = (await session.execute(select(Frame).where(Frame.project_id == project.id))).scalars().all()
    # Предмет введён один раз и держится дальше сам, без повторных объявлений.
    assert all("конверт держит Игнат" in f.attrs["continuity"] for f in frames)
