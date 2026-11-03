"""Каталог просит номер кадра с доски, маршруты доски ждут id кадра: plan_resolve переводит."""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("app.orchestrator.actions")

from app.orchestrator.actions import executor  # noqa: E402
from app.orchestrator.contracts import PlanResolveRefused  # noqa: E402

# Проект 4: номера 1..13, id 1, 2, 235..245.
BOARD = {1: 1, 2: 2, **{n: 232 + n for n in range(3, 14)}}


@pytest.fixture(autouse=True)
def _board(monkeypatch: pytest.MonkeyPatch) -> None:
    async def frame_ids(_pid: int) -> dict[int, int]:
        return dict(BOARD)

    monkeypatch.setattr(executor, "_board_frame_ids", frame_ids)

    async def frames(_pid: int) -> list[dict]:
        # Кадры 3..6 — одна сцена с головой 3, остальные — сами по себе.
        out = []
        for n, fid in BOARD.items():
            head, size = (3, 4) if 3 <= n <= 6 else (n, 1)
            out.append({"number": n, "frame_id": fid, "vo_scene_number": head, "vo_scene_size": size})
        return out

    monkeypatch.setattr(executor, "_board_frames", frames)


def _fill(name: str, params: dict) -> dict:
    return asyncio.run(executor.plan_resolve(name, params))


def test_split_scene_numbers_become_ids() -> None:
    out = _fill("montage.split_scene", {"project_id": 4, "frame_id": 5, "frame_ids": [3, 4, 5, 6]})
    assert out["frame_id"] == 237
    assert out["frame_ids"] == [235, 236, 237, 238]


def test_split_drops_right_part_given_as_visible_frames() -> None:
    out = _fill("montage.split_scene", {"project_id": 4, "frame_id": 5, "frame_ids": [5, 6]})
    assert out == {"project_id": 4, "frame_id": 237}


def test_split_of_single_or_first_frame_is_refused_before_card() -> None:
    with pytest.raises(PlanResolveRefused, match="Кадр 9 и так отдельная сцена"):
        _fill("montage.split_scene", {"project_id": 4, "frame_id": 9})
    with pytest.raises(PlanResolveRefused, match="Кадр 3 — первый в своей сцене"):
        _fill("montage.split_scene", {"project_id": 4, "frame_id": 3})


def test_real_id_without_such_number_stays() -> None:
    out = _fill("montage.merge_scenes", {"project_id": 4, "left_frame_id": 240, "right_frame_id": 3})
    assert out == {"project_id": 4, "left_frame_id": 240, "right_frame_id": 235}


def test_unknown_frame_is_refused_in_words() -> None:
    with pytest.raises(PlanResolveRefused, match="Кадра 99 нет на доске"):
        _fill("montage.split_scene", {"project_id": 4, "frame_id": 99})


def test_non_montage_and_references_untouched() -> None:
    assert _fill("frames.read", {"project_id": 4, "frame_id": 5}) == {"project_id": 4, "frame_id": 5}
    ref = {"project_id": 4, "frame_id": "$s1.right_id"}
    assert _fill("montage.split_scene", ref) == ref
    merge = {"project_id": 4, "left_frame_id": 3, "right_frame_id": 4}
    assert _fill("montage.merge_scenes", merge) == {
        "project_id": 4,
        "left_frame_id": 235,
        "right_frame_id": 236,
    }


def test_card_shows_board_numbers_not_row_ids() -> None:
    from app.orchestrator.agents import approval_steps

    _fill("montage.split_scene", {"project_id": 4, "frame_id": 5, "frame_ids": [3, 4, 5, 6]})
    assert approval_steps._params_text({"project_id": 4, "frame_id": 237}) == "кадр 5; проект #4"
    text = approval_steps._params_text({"project_id": 4, "frame_id": 237, "frame_ids": [235, 236, 237, 238]})
    assert text == "кадр 5; кадры сцены 3, 4, 5, 6; проект #4"


def test_improve_and_generate_scene_go_to_card_not_deletion_ban() -> None:
    from app.orchestrator.contracts import PlanStep, step_is_deletion
    from app.services.action_layer.spec import ensure_builtin_actions, registry

    ensure_builtin_actions()
    from app.orchestrator.agents.wiring import load_actions

    actions, _missing = load_actions()
    for name in ("montage.improve_scene", "montage.generate_scene", "montage.split_scene"):
        step = PlanStep(action=name, params={"project_id": 4, "frame_id": 235}, mutating=True, risk="high")
        assert step_is_deletion(step, actions) is False, name
    assert registry.find("montage.improve_scene") is not None


def test_improve_scene_image_limit_covers_scene_still_and_frames() -> None:
    out = _fill(
        "montage.improve_scene",
        {
            "project_id": 4,
            "frame_id": 3,
            "max_image_ops": 2,
            "prompt": "улучши",
            "image_generator": "x",
            "image_resolution": "2k",
        },
    )
    assert out["frame_id"] == 235 and out["max_image_ops"] == 5  # сцена 3–6: still + 4 кадра
    big = _fill(
        "montage.improve_scene",
        {
            "project_id": 4,
            "frame_id": 9,
            "max_image_ops": 7,
            "prompt": "улучши",
            "image_generator": "x",
            "image_resolution": "2k",
        },
    )
    assert big["max_image_ops"] == 7


def test_done_text_says_images_did_not_start() -> None:
    from app.orchestrator.agents.summarizer import known_text

    body = {"improved": True, "applied": False, "reason": "операций картинок 3 больше заявленных 2"}
    text = known_text("montage.improve_scene", body)
    assert (
        text.startswith("Готово: ")
        and "Картинки не запущены: операций картинок 3 больше заявленных 2." in text
    )
