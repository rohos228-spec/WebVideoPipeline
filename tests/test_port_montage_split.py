"""Порт old/int/chat-v1: деление VO-сцены и отмена деления (montage_board_frames)."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base, Frame, Project
from app.services import db_v2
from app.services.montage_board_frames import (
    split_montage_scene_at,
    split_montage_scene_frames,
)


@pytest.fixture
async def session(tmp_path: Path) -> AsyncSession:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'mf.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await db_v2.migrate_db_v2_schema(conn)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Project:
    data_root = tmp_path / "data"
    data_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("app.settings.settings.data_dir", str(data_root))
    return Project(id=71, slug="montage-frames", topic="t", hero_mode="auto")


def _vo_parent(project_id: int, number: int, uid: str, vo: str) -> Frame:
    return Frame(
        project_id=project_id,
        number=number,
        uuid=uid,
        voiceover_text=vo,
        sort_key=float(number * 10),
        status="planned",
        attrs={"camera_subdivide": {"role": "vo_parent", "parent_uuid": uid}},
    )


def _ordered(frames: list[Frame]) -> list[Frame]:
    return sorted(frames, key=lambda fr: (float(fr.sort_key or 0.0), int(fr.number or 0)))


@pytest.mark.asyncio
async def test_split_scene_at_keeps_numbers(session: AsyncSession, project: Project) -> None:
    from app.services.montage_board import build_montage_board
    from app.services.vo_shot_expand import is_shot_child

    session.add(project)
    a = _vo_parent(project.id, 21, "aa" * 12, "голова")
    b = Frame(
        project_id=project.id,
        number=22,
        uuid="bb" * 12,
        voiceover_text="середина",
        sort_key=220.0,
        status="planned",
        attrs={"camera_subdivide": {"role": "shot", "parent_uuid": "aa" * 12}},
    )
    c = Frame(
        project_id=project.id,
        number=23,
        uuid="cc" * 12,
        voiceover_text="хвост",
        sort_key=230.0,
        status="planned",
        attrs={"camera_subdivide": {"role": "shot", "parent_uuid": "aa" * 12}},
    )
    session.add_all([a, b, c])
    await session.flush()

    result = await split_montage_scene_at(session, project, at_frame_id=int(b.id))
    await session.commit()
    assert result["left_number"] == 21
    assert result["right_number"] == 22
    assert result["left_size"] == 1
    assert result["right_size"] == 2

    rows = _ordered(
        list((await session.execute(select(Frame).where(Frame.project_id == project.id))).scalars())
    )
    assert [fr.number for fr in rows] == [21, 22, 23]
    assert is_shot_child(rows[0]) is False
    assert is_shot_child(rows[1]) is False
    assert is_shot_child(rows[2]) is True

    board = await build_montage_board(session, project)
    by_n = {fr["number"]: fr for fr in board["frames"]}
    assert by_n[21]["vo_scene_number"] == 21
    assert by_n[21]["vo_scene_size"] == 1
    assert by_n[22]["vo_scene_number"] == 22
    assert by_n[23]["vo_scene_number"] == 22
    assert by_n[22]["vo_scene_size"] == 2


@pytest.mark.asyncio
async def test_split_scene_all_each_frame_own_scene(session: AsyncSession, project: Project) -> None:
    from app.services.montage_board import build_montage_board
    from app.services.vo_shot_expand import is_shot_child

    session.add(project)
    a = _vo_parent(project.id, 31, "aa" * 12, "один")
    b = Frame(
        project_id=project.id,
        number=32,
        uuid="bb" * 12,
        voiceover_text="два",
        sort_key=320.0,
        status="planned",
        attrs={"camera_subdivide": {"role": "shot", "parent_uuid": "aa" * 12}},
    )
    c = Frame(
        project_id=project.id,
        number=33,
        uuid="cc" * 12,
        voiceover_text="три",
        sort_key=330.0,
        status="planned",
        attrs={"camera_subdivide": {"role": "shot", "parent_uuid": "aa" * 12}},
    )
    session.add_all([a, b, c])
    await session.flush()

    result = await split_montage_scene_frames(session, project, frame_id=int(a.id))
    await session.commit()
    assert result["scenes"] == 3

    rows = _ordered(
        list((await session.execute(select(Frame).where(Frame.project_id == project.id))).scalars())
    )
    assert [fr.number for fr in rows] == [31, 32, 33]
    assert all(is_shot_child(fr) is False for fr in rows)

    board = await build_montage_board(session, project)
    by_n = {fr["number"]: fr for fr in board["frames"]}
    assert by_n[31]["vo_scene_number"] == 31
    assert by_n[32]["vo_scene_number"] == 32
    assert by_n[33]["vo_scene_number"] == 33
    assert by_n[31]["vo_scene_size"] == 1
    assert by_n[32]["vo_scene_size"] == 1
    assert by_n[33]["vo_scene_size"] == 1


@pytest.mark.asyncio
async def test_split_all_visible_ids_hides_extra_shots(session: AsyncSession, project: Project) -> None:
    from app.services.montage_board import build_montage_board
    from app.services.vo_shot_expand import _cs, is_shot_child

    session.add(project)
    a = _vo_parent(project.id, 41, "aa" * 12, "один")
    b = Frame(
        project_id=project.id,
        number=42,
        uuid="bb" * 12,
        voiceover_text="два",
        sort_key=420.0,
        status="planned",
        attrs={"camera_subdivide": {"role": "shot", "parent_uuid": "aa" * 12}},
    )
    c = Frame(
        project_id=project.id,
        number=43,
        uuid="cc" * 12,
        voiceover_text="три",
        sort_key=430.0,
        status="planned",
        attrs={"camera_subdivide": {"role": "shot", "parent_uuid": "aa" * 12}},
    )
    extra = Frame(
        project_id=project.id,
        number=44,
        uuid="dd" * 12,
        voiceover_text="хвост",
        sort_key=440.0,
        status="planned",
        attrs={"camera_subdivide": {"role": "shot", "parent_uuid": "aa" * 12}},
    )
    session.add_all([a, b, c, extra])
    await session.flush()

    result = await split_montage_scene_frames(
        session,
        project,
        frame_id=int(a.id),
        frame_ids=[int(a.id), int(b.id), int(c.id)],
    )
    await session.commit()
    assert result["scenes"] == 3

    rows = _ordered(
        list((await session.execute(select(Frame).where(Frame.project_id == project.id))).scalars())
    )
    by_n = {int(fr.number): fr for fr in rows}
    assert is_shot_child(by_n[41]) is False
    assert is_shot_child(by_n[42]) is False
    assert is_shot_child(by_n[43]) is False
    assert is_shot_child(by_n[44]) is True
    assert bool(_cs(by_n[44]).get("leftover")) is True

    board = await build_montage_board(session, project)
    shown = {fr["number"]: fr for fr in board["frames"]}
    assert shown[44]["shot_leftover"] is True
    assert shown[41]["vo_scene_size"] == 2
    assert shown[42]["vo_scene_number"] == 42
    assert shown[43]["vo_scene_number"] == 43


def _three_frame_scene(project_id: int) -> tuple[Frame, Frame, Frame]:
    a = _vo_parent(project_id, 21, "aa" * 12, "голова")
    kids = []
    for num, uid, vo in ((22, "bb" * 12, "середина"), (23, "cc" * 12, "хвост")):
        kids.append(
            Frame(
                project_id=project_id,
                number=num,
                uuid=uid,
                voiceover_text=vo,
                sort_key=float(num * 10),
                status="planned",
                attrs={"camera_subdivide": {"role": "shot", "parent_uuid": "aa" * 12}},
            )
        )
    return a, kids[0], kids[1]


async def _attrs_by_number(session: AsyncSession, project_id: int) -> dict[int, dict]:
    from copy import deepcopy

    rows = (await session.execute(select(Frame).where(Frame.project_id == project_id))).scalars().all()
    return {int(fr.number): deepcopy(dict(fr.attrs or {})) for fr in rows}


@pytest.mark.asyncio
async def test_split_undo_restores_scenes_and_keeps_paid_media(
    session: AsyncSession, project: Project
) -> None:
    from app.models import Artifact, ArtifactKind
    from app.services.montage_board_frames import split_undo_info, undo_montage_split

    session.add(project)
    a, b, c = _three_frame_scene(project.id)
    session.add_all([a, b, c])
    await session.flush()
    session.add_all(
        [
            Artifact(
                project_id=project.id,
                frame_id=b.id,
                kind=ArtifactKind.scene_image,
                uuid="img-b",
                path="x/b.png",
            ),
            Artifact(
                project_id=project.id,
                frame_id=c.id,
                kind=ArtifactKind.scene_video,
                uuid="vid-c",
                path="x/c.mp4",
            ),
        ]
    )
    await session.commit()
    before = await _attrs_by_number(session, project.id)

    res = await split_montage_scene_at(session, project, at_frame_id=int(b.id))
    await session.commit()
    assert res["undo_id"]
    assert (res["left_ordinal"], res["right_ordinal"]) == (1, 2)
    assert await _attrs_by_number(session, project.id) != before
    assert split_undo_info(project)["can_undo"] is True

    out = await undo_montage_split(session, project, undo_id=res["undo_id"])
    await session.commit()
    assert out["ok"] and out["paid_media_kept"] == {"images": 1, "videos": 1, "total": 2}
    assert await _attrs_by_number(session, project.id) == before
    assert split_undo_info(project)["can_undo"] is False
    arts = (await session.execute(select(Artifact).where(Artifact.project_id == project.id))).scalars().all()
    assert {(x.uuid, x.frame_id) for x in arts} == {("img-b", b.id), ("vid-c", c.id)}
    with pytest.raises(RuntimeError):
        await undo_montage_split(session, project)


@pytest.mark.asyncio
async def test_split_undo_refuses_when_frames_were_edited_after_split(
    session: AsyncSession, project: Project
) -> None:
    from app.services.montage_board_frames import undo_montage_split

    session.add(project)
    a, b, c = _three_frame_scene(project.id)
    session.add_all([a, b, c])
    await session.flush()
    res = await split_montage_scene_at(session, project, at_frame_id=int(b.id))
    await session.commit()
    c.attrs = {
        **dict(c.attrs or {}),
        "camera_subdivide": {"role": "shot", "parent_uuid": "bb" * 12, "plan": "крупный"},
    }
    from sqlalchemy.orm.attributes import flag_modified

    flag_modified(c, "attrs")
    await session.commit()
    snapshot = await _attrs_by_number(session, project.id)
    with pytest.raises(RuntimeError, match="правили"):
        await undo_montage_split(session, project, undo_id=res["undo_id"])
    assert await _attrs_by_number(session, project.id) == snapshot


@pytest.mark.asyncio
async def test_split_undo_only_latest_and_split_all_is_undoable(
    session: AsyncSession, project: Project
) -> None:
    from app.services.montage_board_frames import undo_montage_split

    session.add(project)
    a, b, c = _three_frame_scene(project.id)
    session.add_all([a, b, c])
    await session.flush()
    await session.commit()
    before = await _attrs_by_number(session, project.id)
    r = await split_montage_scene_frames(session, project, frame_id=int(a.id))
    await session.commit()
    assert r["undo_id"] and r["parent_ordinal"] == 1
    with pytest.raises(RuntimeError, match="последнее"):
        await undo_montage_split(session, project, undo_id="not-the-latest")
    await undo_montage_split(session, project, undo_id=r["undo_id"])
    await session.commit()
    assert await _attrs_by_number(session, project.id) == before
