from __future__ import annotations

import asyncio

import pytest

from app.orchestrator.agents import music_scope


@pytest.mark.parametrize(
    "text, direction",
    [
        ("перемонтируй с музыкой потише", -1),
        ("сделай музыку погромче в 4 проекте", 1),
        ("приглуши музыку в проекте 4", -1),
        ("поставь музыку на 30", 0),
        ("сделай субтитры побольше", 0),
        ("перемонтируй", 0),
    ],
)
def test_direction(text: str, direction: int) -> None:
    assert music_scope.volume_direction(text) == direction


def test_plan_shifts_from_current_and_remounts(monkeypatch) -> None:
    async def level(_pid: int) -> int:
        return 70

    monkeypatch.setattr(music_scope, "_current_level", level)
    plan = asyncio.run(music_scope.music_volume_plan("перемонтируй с музыкой потише", 4))
    assert [s.action for s in plan] == ["montage.set_assemble_params", "montage.run_montage"]
    assert plan[0].params == {"project_id": 4, "bgm_level": 46}
    only = asyncio.run(music_scope.music_volume_plan("музыку погромче в 7 проекте", None))
    assert [s.action for s in only] == ["montage.set_assemble_params"]
    assert only[0].params == {"project_id": 7, "bgm_level": 94}


def test_quieter_never_mutes() -> None:
    assert music_scope.shifted_level(20, -1) == 13
    assert music_scope.shifted_level(6, -1) == 5
    assert music_scope.shifted_level(5, -1) == 5
    assert music_scope.shifted_level(0, 1) == 5
