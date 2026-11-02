from unittest.mock import AsyncMock

import pytest

from app.models import Project
from app.services.node_step_params import build_duration_params_block, build_split_params_block
from app.web.routers.db_browser import _apply_set_briefing, _execute_orchestrator_reply


def test_apply_set_briefing():
    p = Project(slug="test-proj", title="Test", meta={})

    # 1. Update duration, frames, voiceover, hero, sfx
    res = _apply_set_briefing(p, {
        "duration": 45,
        "target_frames": 12,
        "voiceover_mode": "full",
        "hero_mode": "hero",
        "items_consistent": False,
        "structure": "динамичный клип",
        "visual_style": "3D мультфильм",
        "sfx_enabled": False,
    })

    assert "duration=45.0s" in res
    assert "frames=12" in res
    assert "hero_mode=hero" in res
    assert "sfx=off" in res

    meta = p.meta or {}
    briefing = meta.get("briefing") or {}
    nsp = meta.get("node_step_params") or {}

    assert briefing["duration_seconds"] == 45.0
    assert briefing["target_frames"] == 12
    assert briefing["hero_mode"] == "hero"
    assert briefing["sfx_enabled"] is False
    assert p.hero_mode == "hero"
    assert "sfx_plan" in meta.get("disabled_nodes", [])
    assert nsp["plan"]["duration_seconds"] == 45.0
    assert nsp["script"]["duration_seconds"] == 45.0

    # 2. Check duration block includes briefing lines
    block_plan = build_duration_params_block(p, "plan", header="Сценарий")
    assert "Целевое количество сцен/кадров: 12" in block_plan
    assert "Длина 45 секунд" in block_plan
    assert "Структура и темп сцен: динамичный клип" in block_plan
    assert "Визуальный стиль: 3D мультфильм" in block_plan

    block_split = build_split_params_block(p)
    assert "Темп склеек: динамичный клип" in block_split


@pytest.mark.asyncio
async def test_execute_orchestrator_reply_choices(monkeypatch):
    p = Project(slug="test-proj-2", title="Test 2", meta={})
    p.id = 42

    mock_session = AsyncMock()
    monkeypatch.setattr("app.web.routers.db_browser.commit_with_retry", AsyncMock())

    reply = (
        "Давай определимся с хронометражем ролика. Сколько секунд планируем?\n\n"
        '{"actions":[{"set_briefing":{"duration":30}}], '
        '"choices":["15–20с (Shorts)", "30–45с (Стандарт)", "60с", "Свой ответ"]}'
    )

    res = await _execute_orchestrator_reply(
        mock_session,
        p,
        reply,
        diagnose_only=False,
        canvas_keymap={},
        diag_lines=[],
        body_message="брифинг проекта",
    )

    assert res["choices"] == ["15–20с (Shorts)", "30–45с (Стандарт)", "60с", "Свой ответ"]
    assert "Давай определимся с хронометражем" in res["reply"]
    # Check JSON block is stripped from human reply
    assert '{"actions"' not in res["reply"]
    assert '{"choices"' not in res["reply"]
    assert (p.meta or {}).get("briefing", {}).get("duration_seconds") == 30.0
