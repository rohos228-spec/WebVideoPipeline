"""Still сцены в своём слоте (slot=parent): свой файл, свой ключ, не второй N:1."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base, Frame, Project
from app.services.montage_board import build_montage_board
from app.services.montage_board_apply import order_montage_pending_ops
from app.services.montage_board_meta import normalize_queue_ops, slot_key_from_op
from app.services.plan_shot2 import (
    find_parent_still_image,
    find_shot1_image,
    is_parent_slot,
    resolve_coverage_parent_png,
)
from app.services.scan_frames import newest_frame_image_path

PARENT_UID = "aa" * 12
PNG = b"\x89PNG" + b"x" * 400


@pytest.fixture
async def session(tmp_path: Path) -> AsyncSession:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'parent.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Project:
    data_root = tmp_path / "data"
    data_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("app.settings.settings.data_dir", str(data_root))
    p = Project(id=99, slug="parent-slot", topic="Тест", hero_mode="auto")
    p.data_dir.mkdir(parents=True, exist_ok=True)
    return p


def _parent_and_child(project: Project) -> list[Frame]:
    parent = Frame(
        project_id=project.id,
        number=1,
        uuid=PARENT_UID,
        voiceover_text="вошёл в кабинет",
        status="planned",
        attrs={
            "characters": "c02",
            "персонажи": "c02",
            "предметы": "i01",
            "camera_subdivide": {"role": "vo_parent", "parent_uuid": PARENT_UID, "shot_id": "1-K1"},
        },
    )
    child = Frame(
        project_id=project.id,
        number=2,
        uuid="bb" * 12,
        voiceover_text="достал папку",
        status="planned",
        attrs={
            "camera_subdivide": {
                "role": "shot",
                "parent_uuid": PARENT_UID,
                "shot_id": "1-K2",
                "coverage_parent_id": "1-K1",
            },
        },
    )
    return [parent, child]


# --- plan_shot2 (chat-v1 tests/test_plan_shot2.py) ---


def test_parent_still_is_not_shot1(tmp_path: Path) -> None:
    scenes = tmp_path / "scenes"
    scenes.mkdir()
    (scenes / "frame_001_abc12345.png").write_bytes(b"shot1")
    (scenes / "frame_001_parent_deadbeef.png").write_bytes(b"master")

    assert find_shot1_image(scenes, 1).name == "frame_001_abc12345.png"
    assert find_parent_still_image(scenes, 1).name == "frame_001_parent_deadbeef.png"
    assert find_parent_still_image(scenes, 2) is None
    assert resolve_coverage_parent_png(scenes, 1).name == "frame_001_parent_deadbeef.png"
    assert newest_frame_image_path(scenes, 1).name == "frame_001_abc12345.png"
    assert is_parent_slot("parent") and is_parent_slot(" Parent ") and not is_parent_slot("")


def test_resolve_parent_png_falls_back_to_shot1(tmp_path: Path) -> None:
    scenes = tmp_path / "scenes"
    scenes.mkdir()
    (scenes / "frame_002_abc12345.png").write_bytes(b"shot1")
    assert resolve_coverage_parent_png(scenes, 2).name == "frame_002_abc12345.png"
    assert resolve_coverage_parent_png(scenes, 9) is None


# --- очередь (chat-v1 tests/test_montage_queue_persist.py, test_scene_action_with_images.py) ---


def test_slot_key_from_op_image_and_video() -> None:
    assert slot_key_from_op({"type": "image_regen", "frame_number": 9, "shot": 1}) == "9:image1"
    assert (
        slot_key_from_op({"type": "image_regen", "frame_number": 9, "shot": 1, "slot": "parent"})
        == "9:image_parent"
    )
    assert slot_key_from_op({"type": "video_regen", "frame_number": 9, "shot": 1}) == "9:1"
    kept = normalize_queue_ops([{"type": "image_regen", "frame_number": 3, "shot": 1, "slot": "parent"}])
    assert kept == [{"type": "image_regen", "frame_number": 3, "shot": 1, "slot": "parent"}]


def test_parent_and_shot1_ops_of_same_frame_are_both_kept() -> None:
    ops = normalize_queue_ops(
        [
            {"type": "image_ai_change", "frame_number": 1, "shot": 1, "slot": "parent"},
            {"type": "image_ai_change", "frame_number": 1, "shot": 1},
        ]
    )
    assert {slot_key_from_op(op) for op in ops} == {"1:image_parent", "1:image1"}


def test_parent_slot_sorts_before_child_shot() -> None:
    ordered = order_montage_pending_ops(
        [
            {"type": "image_ai_change", "frame_number": 2, "shot": 1},
            {"type": "image_ai_change", "frame_number": 1, "shot": 1},
            {"type": "image_ai_change", "frame_number": 1, "shot": 1, "slot": "parent"},
        ]
    )
    imgs = [op for op in ordered if op["type"] == "image_ai_change"]
    assert imgs[0].get("slot") == "parent"
    assert imgs[0]["frame_number"] == 1
    assert imgs[1]["frame_number"] == 1
    assert imgs[1].get("slot") != "parent"
    assert imgs[2]["frame_number"] == 2


def test_apply_passes_slot_to_both_prepare_calls() -> None:
    src = (Path(__file__).resolve().parents[1] / "app/services/montage_board_apply.py").read_text(
        encoding="utf-8"
    )
    assert src.count('slot=str(op.get("slot") or "")') >= 2


# --- доска (chat-v1 tests/test_montage_board.py::test_montage_board_group_refs_on_vo_cell) ---


@pytest.mark.asyncio
async def test_montage_board_group_refs_on_vo_cell(project: Project, session: AsyncSession) -> None:
    """Рефы VO-ячейки: still родителя только у детей; персонажи/предметы на группе."""
    scenes = project.data_dir / "scenes"
    chars_dir = project.data_dir / "characters"
    items_dir = project.data_dir / "items"
    for d in (scenes, chars_dir, items_dir):
        d.mkdir(parents=True, exist_ok=True)
    (scenes / "frame_001_shot1.png").write_bytes(b"png-shot1")
    (scenes / "frame_001_parent.png").write_bytes(b"png-parent")
    (scenes / "frame_002_child.png").write_bytes(b"png-child")
    (chars_dir / "c02.png").write_bytes(b"png-c02")
    (items_dir / "i01.png").write_bytes(b"png-i01")
    stranger = Frame(
        project_id=project.id,
        number=39,
        uuid="cc" * 12,
        voiceover_text="другая ячейка",
        status="planned",
        attrs={
            "characters": "c09",
            "camera_subdivide": {
                "role": "vo_parent",
                "parent_uuid": "cc" * 12,
                "shot_id": "1-S13-K1",
                "coverage_parent_id": "1-K1",
            },
        },
    )
    session.add(project)
    session.add_all([*_parent_and_child(project), stranger])
    await session.flush()

    board = await build_montage_board(session, project)
    p_row, c_row, s_row = board["frames"]
    assert p_row["ref_parent"] is None
    assert p_row["image_shot1_url"]
    assert p_row["image_parent_url"]
    assert "parent" in (p_row["image_parent_url"] or "")
    assert p_row["image_parent_url"] != p_row["image_shot1_url"]
    assert p_row["group_character_refs"][0]["id"] == "c02"
    assert p_row["group_character_refs"][0]["code"] == "c02"
    assert p_row["item_refs"][0]["id"] == "i01"
    assert c_row["ref_parent"]["number"] == 1
    assert c_row["ref_parent"]["image_url"]
    assert "parent" in (c_row["ref_parent"]["image_url"] or "")
    assert c_row["image_shot1_url"]
    assert c_row["image_shot1_url"] != c_row["ref_parent"]["image_url"]
    assert not s_row.get("image_parent_url")


# --- файлы слота ---


@pytest.mark.asyncio
async def test_parent_upload_delete_move_do_not_touch_shot1(project: Project, session: AsyncSession) -> None:
    from app.services.montage_board_assets import (
        delete_scene_image,
        move_scene_image,
        save_scene_image_upload,
    )

    session.add(project)
    session.add_all(_parent_and_child(project))
    await session.flush()
    scenes = project.data_dir / "scenes"
    shot1 = await save_scene_image_upload(session, project, 1, shot=1, content=PNG, suffix=".png")
    parent = await save_scene_image_upload(
        session, project, 1, shot=1, content=PNG + b"p", suffix=".png", slot="parent"
    )
    assert parent.name.startswith("frame_001_parent_")
    assert shot1.is_file() and parent.is_file()
    assert find_shot1_image(scenes, 1) == shot1
    assert find_parent_still_image(scenes, 1) == parent

    # Новый still сцены заменяет только старый still, shot1 остаётся.
    parent2 = await save_scene_image_upload(
        session, project, 1, shot=1, content=PNG + b"q", suffix=".png", slot="parent"
    )
    assert shot1.is_file() and not parent.is_file()
    assert find_parent_still_image(scenes, 1) == parent2

    assert await delete_scene_image(session, project, 1, shot=1, slot="parent")
    assert find_parent_still_image(scenes, 1) is None
    assert find_shot1_image(scenes, 1) == shot1

    # shot1 → слот still того же кадра: move, промт кадра не трогаем.
    out = await move_scene_image(
        session, project, from_frame=1, from_shot=1, to_frame=1, to_shot=1, to_slot="parent"
    )
    assert out["ok"] and out["mode"] == "move" and out["prompts_moved"] is False
    assert find_shot1_image(scenes, 1) is None
    moved = find_parent_still_image(scenes, 1)
    assert moved is not None and moved.read_bytes() == PNG
    same = await move_scene_image(
        session,
        project,
        from_frame=1,
        from_shot=1,
        to_frame=1,
        to_shot=1,
        from_slot="parent",
        to_slot="parent",
    )
    assert same["ok"] is False


# --- перегенерация в слот ---


@pytest.mark.asyncio
async def test_parent_regen_writes_own_file_and_highlight(project: Project, session: AsyncSession) -> None:
    from app.services.montage_board_assets import save_scene_image_upload
    from app.services.montage_board_regen import finalize_image_regen, prepare_image_regen

    session.add(project)
    session.add_all(_parent_and_child(project))
    await session.flush()
    scenes = project.data_dir / "scenes"
    shot1 = await save_scene_image_upload(session, project, 1, shot=1, content=PNG, suffix=".png")

    prep = await prepare_image_regen(
        session, project, 1, shot=1, pinned_prompt="wide shot office", slot="parent"
    )
    assert prep.slot == "parent"
    assert prep.file_path.name.startswith("frame_001_parent_")
    assert prep.prompt_id_prefix.endswith("-P")
    prep.file_path.write_bytes(PNG + b"gen")
    out = await finalize_image_regen(session, project, prep, prep.file_path)
    assert out["highlight"] == "1:image_parent"
    assert shot1.is_file()
    assert find_shot1_image(scenes, 1) == shot1
    assert find_parent_still_image(scenes, 1) == prep.file_path

    # Шот ячейки опирается на still сцены, а не на shot1 родителя.
    child = await prepare_image_regen(session, project, 2, shot=1, pinned_prompt="close up folder")
    assert prep.file_path in child.refs
    assert shot1 not in child.refs
    assert child.file_path.name.startswith("frame_002_") and "_parent_" not in child.file_path.name


@pytest.mark.asyncio
async def test_child_regen_without_parent_still_asks_for_parent(
    project: Project, session: AsyncSession
) -> None:
    from app.services.montage_board_regen import prepare_image_regen

    session.add(project)
    session.add_all(_parent_and_child(project))
    await session.flush()
    with pytest.raises(RuntimeError, match="сначала сгенерируйте родителя"):
        await prepare_image_regen(session, project, 2, shot=1, pinned_prompt="close up folder")
