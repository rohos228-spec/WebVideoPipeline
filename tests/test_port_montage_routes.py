"""Порт old/int/chat-v1: ручки доски монтажа для сцен и отчёт «Кадры».

Ручки зовутся напрямую (без сервера), GPT подменён: ни одного платного вызова.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base, Frame, Project
from app.services import db_v2
from app.web.routers import project_ops

PARENT = "aa" * 12
KID_B = "bb" * 12
KID_C = "cc" * 12


@pytest.fixture
async def session(tmp_path: Path) -> AsyncSession:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'routes.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await db_v2.migrate_db_v2_schema(conn)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


@pytest.fixture
def events(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    seen: list[dict] = []

    async def fake_publish(project_id: int, **kwargs):  # noqa: ANN003
        seen.append({"project_id": project_id, **kwargs})

    monkeypatch.setattr(project_ops, "publish_project_event", fake_publish)
    return seen


@pytest.fixture
def no_gpt(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Любой вызов GPT в тесте без своей подмены — ошибка теста, не деньги."""
    asked: list[str] = []

    async def refuse(text: str, **kwargs):  # noqa: ANN003
        asked.append(text)
        raise AssertionError("GPT не должен вызываться")

    monkeypatch.setattr("app.services.gpt_client.gpt_ask_fresh", refuse)
    return asked


@pytest.fixture
async def project(session: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Project:
    data_root = tmp_path / "data"
    data_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("app.settings.settings.data_dir", str(data_root))
    p = Project(id=91, slug="port-routes", topic="t", hero_mode="auto")
    session.add(p)
    session.add_all(
        [
            Frame(
                project_id=91,
                number=1,
                uuid=PARENT,
                voiceover_text="Следователь вошёл в архив.",
                sort_key=10.0,
                status="planned",
                attrs={"camera_subdivide": {"role": "vo_parent", "parent_uuid": PARENT}},
            ),
            Frame(
                project_id=91,
                number=2,
                uuid=KID_B,
                voiceover_text="Снял папку с полки.",
                sort_key=20.0,
                status="planned",
                attrs={"camera_subdivide": {"role": "shot", "parent_uuid": PARENT}},
            ),
            Frame(
                project_id=91,
                number=3,
                uuid=KID_C,
                voiceover_text="И читает документ.",
                sort_key=30.0,
                status="planned",
                attrs={"camera_subdivide": {"role": "shot", "parent_uuid": PARENT}},
            ),
        ]
    )
    await session.commit()
    return p


async def _frames(session: AsyncSession) -> dict[int, Frame]:
    rows = (await session.execute(select(Frame).where(Frame.project_id == 91))).scalars().all()
    return {int(fr.number): fr for fr in rows}


@pytest.mark.asyncio
async def test_split_route_and_undo_keep_numbers(
    session: AsyncSession, project: Project, events: list[dict], no_gpt: list[str]
) -> None:
    from app.services.vo_shot_expand import is_shot_child

    frames = await _frames(session)
    out = await project_ops.montage_board_split_scenes(
        91, {"frame_id": int(frames[2].id)}, session
    )
    # Ответ — те поля, что читает splitMontageScene на фронте.
    assert out["ok"] is True
    assert (out["left_number"], out["right_number"]) == (1, 2)
    assert (out["left_size"], out["right_size"]) == (1, 2)
    assert out["undo_id"]
    assert events[-1]["payload"]["montage_scenes_split"] is True
    session.expire_all()
    frames = await _frames(session)
    assert sorted(frames) == [1, 2, 3]
    assert is_shot_child(frames[2]) is False
    assert is_shot_child(frames[3]) is True

    undo = await project_ops.montage_board_undo_split(91, {"undo_id": out["undo_id"]}, session)
    assert undo["ok"] is True and undo["restored_frames"] >= 1
    session.expire_all()
    frames = await _frames(session)
    assert is_shot_child(frames[2]) is True
    assert no_gpt == []


@pytest.mark.asyncio
async def test_split_route_explode_and_bad_body(
    session: AsyncSession, project: Project, events: list[dict], no_gpt: list[str]
) -> None:
    frames = await _frames(session)
    out = await project_ops.montage_board_split_scenes(
        91, {"frame_id": int(frames[1].id), "all": True}, session
    )
    assert out["ok"] is True and out["scenes"] == 3 and out["parent_number"] == 1
    with pytest.raises(HTTPException) as exc:
        await project_ops.montage_board_split_scenes(91, {}, session)
    assert exc.value.status_code == 400
    with pytest.raises(HTTPException) as exc:
        await project_ops.montage_board_split_scenes(
            91, {"frame_id": int(frames[1].id), "frame_ids": ["x"]}, session
        )
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_split_route_refuses_while_step_runs(
    session: AsyncSession, project: Project, events: list[dict]
) -> None:
    from app.models import ProjectStatus
    from app.services.project_state import is_running_status

    running = next(s for s in ProjectStatus if is_running_status(s))
    project.status = running
    await session.commit()
    frames = await _frames(session)
    with pytest.raises(HTTPException) as exc:
        await project_ops.montage_board_split_scenes(91, {"frame_id": int(frames[2].id)}, session)
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_scene_action_generate_route_writes_chain(
    session: AsyncSession, project: Project, events: list[dict], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_ask(text: str, **kwargs):  # noqa: ANN003
        return json.dumps(
            {
                "ops": [
                    {
                        "frame_uuid": PARENT,
                        "fields": {
                            "главное_действие": (
                                "1. архив — входит в архив\n(Следователь вошёл в архив.)\n"
                                "2. архив — снимает папку\n(Снял папку с полки.)\n"
                                "3. архив — читает документ\n(И читает документ.)"
                            )
                        },
                    }
                ]
            },
            ensure_ascii=False,
        )

    monkeypatch.setattr("app.services.gpt_client.gpt_ask_fresh", fake_ask)
    frames = await _frames(session)
    out = await project_ops.montage_board_scene_action_generate(
        91, int(frames[1].id), {"replace_ns": []}, session
    )
    # Поля ответа generateSceneAction.
    for key in ("ok", "frame_id", "frame_number", "replace_ns", "chain", "shots", "frame_numbers"):
        assert key in out, key
    assert out["frame_number"] == 1
    assert "снимает папку" in out["chain"]
    assert events[-1]["payload"]["montage_scene_action_generate"] is True


@pytest.mark.asyncio
async def test_generate_with_images_fallback_returns_parent_slot_op_once(
    session: AsyncSession, project: Project, events: list[dict], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def empty(text: str, **kwargs):  # noqa: ANN003
        return "нет сцен"

    monkeypatch.setattr("app.services.gpt_client.gpt_ask_fresh", empty)
    frames = await _frames(session)
    out = await project_ops.montage_board_scene_generate_with_images(
        91,
        int(frames[1].id),
        {"prompt": "входит в архив → снимает папку → читает", "frame_ids": [int(frames[1].id)]},
        session,
    )
    assert out["ok"] is True
    assert out["started"] is False and out["already_running"] is False
    assert out["images"] == len(out["image_ops"]) >= 1
    # Still сцены идёт в свой слот (slot=parent) и первым; второго N:1 нет.
    ops = out["image_ops"]
    parents = [op for op in ops if op.get("slot") == "parent"]
    assert len(parents) <= 1
    if parents:
        assert ops[0].get("slot") == "parent"
    keys = [(op["frame_number"], op["shot"], op.get("slot") or "") for op in ops]
    assert len(keys) == len(set(keys))
    assert all(str(op["type"]).startswith("image_") for op in out["image_ops"])


@pytest.mark.asyncio
async def test_scene_improve_paid_gate_is_402_not_fallback(
    session: AsyncSession, project: Project, events: list[dict], monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services import spend_guard

    asked: list[str] = []

    async def blocked(text: str, **kwargs):  # noqa: ANN003
        asked.append(text)
        raise spend_guard.PaidCallBlocked("kill_switch_engaged", "платные вызовы остановлены")

    monkeypatch.setattr("app.services.gpt_client.gpt_ask_fresh", blocked)
    frames = await _frames(session)
    before = {n: dict(fr.attrs or {}) for n, fr in frames.items()}
    with pytest.raises(HTTPException) as exc:
        await project_ops.montage_board_scene_improve(
            91, int(frames[1].id), {"prompt": "входит → читает"}, session
        )
    assert exc.value.status_code == 402
    assert "остановлены" in str(exc.value.detail)
    # Первый же отказ шлюза останавливает цепочку: дальше GPT не дёргаем.
    assert len(asked) == 1
    assert not events
    await session.rollback()
    session.expire_all()
    after = await _frames(session)
    assert {n: dict(fr.attrs or {}) for n, fr in after.items()} == before

    # Тот же стоп через «сцены с картинками»: не мягкий fallback, а 402.
    with pytest.raises(HTTPException) as exc:
        await project_ops.montage_board_scene_generate_with_images(
            91, int(frames[1].id), {"prompt": "входит → читает"}, session
        )
    assert exc.value.status_code == 402


@pytest.mark.asyncio
async def test_coverage_vo_span_route_saves_and_clears(
    session: AsyncSession, project: Project, events: list[dict], no_gpt: list[str]
) -> None:
    from app.services.montage_scene_editor import VO_SPAN_ATTR

    full = "Следователь вошёл в архив. Снял папку с полки. И читает документ."
    out = await project_ops.montage_board_apply_coverage(
        91,
        {
            "type": "coverage_vo_span",
            "frame_number": 1,
            "shot": 1,
            "start": 0,
            "end": 26,
            "text": "Следователь вошёл в архив.",
            "full": full,
        },
        session,
    )
    assert out["ok"] is True and out["highlight"] == "1:vo_span"
    assert out["frame"]["vo_span"]["text"] == "Следователь вошёл в архив."
    session.expire_all()
    frames = await _frames(session)
    assert frames[1].attrs[VO_SPAN_ATTR]["text"] == "Следователь вошёл в архив."

    await project_ops.montage_board_apply_coverage(
        91, {"type": "coverage_vo_span", "frame_number": 1, "shot": 1, "clear": True}, session
    )
    session.expire_all()
    frames = await _frames(session)
    assert VO_SPAN_ATTR not in (frames[1].attrs or {})


@pytest.mark.asyncio
async def test_shots_report_route(session: AsyncSession, project: Project) -> None:
    from app.web.routers.projects import project_shots_report

    with pytest.raises(HTTPException) as exc:
        await project_shots_report(91, session)
    assert exc.value.status_code == 404
    reports = Path(project.data_dir) / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / "shots-report.html").write_text("<html>кадры</html>", encoding="utf-8")
    resp = await project_shots_report(91, session)
    assert resp.media_type == "text/html"
    assert "кадры" in resp.body.decode("utf-8")
    with pytest.raises(HTTPException) as exc:
        await project_shots_report(404404, session)
    assert exc.value.status_code == 404


def test_routes_are_on_routers() -> None:
    from app.web.routers import projects

    paths = {getattr(r, "path", "") for r in [*project_ops.router.routes, *projects.router.routes]}
    for path in (
        "/projects/{project_id}/montage-board/scenes/split",
        "/projects/{project_id}/montage-board/scenes/split-undo",
        "/projects/{project_id}/montage-board/frames/{frame_id}/scene-action-generate",
        "/projects/{project_id}/montage-board/frames/{frame_id}/scene-generate-with-images",
        "/projects/{project_id}/montage-board/frames/{frame_id}/scene-improve",
        "/projects/{project_id}/shots-report",
    ):
        assert path in paths, path
