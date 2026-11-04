from unittest.mock import AsyncMock

import pytest

from app.models import Project
from app.services.node_step_params import (
    build_duration_params_block,
    build_split_params_block,
    subtitles_enabled_for_project,
)
from app.web.routers.db_browser import (
    _apply_set_briefing,
    _clean_orchestrator_human_reply,
    _execute_orchestrator_reply,
)


def test_apply_set_briefing():
    p = Project(slug="test-proj", title="Test", meta={}, prompt_overrides={})

    # 1. Update duration, frames, scenario prompt, voiceover, hero, montage, subtitles, sfx
    res = _apply_set_briefing(p, {
        "duration": 45,
        "target_frames": 12,
        "aspect_ratio": "16:9",
        "plan_prompt": "cinematic_story_plan",
        "scenario_type": "Кинематографичная история",
        "voiceover_mode": "full",
        "hero_mode": "Один постоянный Главный Герой",
        "items_consistent": False,
        "visual_style": "3D мультфильм",
        "structure": "динамичный клип",
        "montage": "клиповый с быстрыми склейками",
        "subtitles_enabled": True,
        "sfx_enabled": False,
    })

    assert "duration=45.0s" in res
    assert "frames=12" in res
    assert "aspect=16_9" in res
    assert "plan_prompt=cinematic_story_plan" in res
    assert "hero_mode=hero" in res
    assert "subs=on" in res
    assert "sfx=off" in res

    meta = p.meta or {}
    briefing = meta.get("briefing") or {}
    nsp = meta.get("node_step_params") or {}

    assert briefing["duration_seconds"] == 45.0
    assert briefing["target_frames"] == 12
    assert briefing["aspect_ratio"] == "16_9"
    assert p.aspect_ratio == "16_9"
    assert meta["aspect_ratio"] == "16_9"
    assert meta["visual_style"] == "3D мультфильм"
    assert briefing["plan_prompt"] == "cinematic_story_plan"
    assert briefing["scenario_type"] == "Кинематографичная история"
    assert briefing["hero_mode"] == "hero"
    assert briefing["subtitles_enabled"] is True
    assert briefing["sfx_enabled"] is False
    assert p.hero_mode == "hero"
    assert (p.prompt_overrides or {}).get("plan") == "cinematic_story_plan"
    assert "sfx_plan" in meta.get("disabled_nodes", [])
    assert nsp["plan"]["duration_seconds"] == 45.0
    assert nsp["script"]["duration_seconds"] == 45.0
    assert nsp["assemble"]["subtitles_enabled"] is True
    assert subtitles_enabled_for_project(p) is True

    # Check hero_mode normalization for 'no_hero'
    _apply_set_briefing(p, {"hero_mode": "Без персонажей (только природа)"})
    assert p.hero_mode == "no_hero"

    # 2. Check duration block includes briefing lines
    block_plan = build_duration_params_block(p, "plan", header="Сценарий")
    assert "Целевое количество сцен/кадров: 12" in block_plan
    assert "Длина 45 секунд" in block_plan
    assert "Тип сценария: Кинематографичная история" in block_plan
    assert "Формат кадра (соотношение сторон): 16_9" in block_plan
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


def test_clean_orchestrator_human_reply_nested_json():
    # 1. Nested set_briefing
    t1 = '{"actions":[{"set_briefing":{"plan_prompt":"cinematic_story_plan"}}]}\n\nЗаписал: кинематографичная история'
    res1 = _clean_orchestrator_human_reply(t1)
    assert res1 == "Записал: кинематографичная история"

    # 2. Multiple nested actions + choices (from screenshot)
    t2 = (
        '{"actions":[{"set_briefing":{"hero_mode":"no_hero"}},{"set_option":{"key":"hero_mode","value":"no_hero"}}],'
        '"choices":["Кинематографичный фотореализм","3D мультфильм / CGI"]}\n\n'
        "Записал: без постоянных героев"
    )
    res2 = _clean_orchestrator_human_reply(t2)
    assert res2 == "Записал: без постоянных героев"

    # 3. Stray trailing bracket
    t3 = ')}] Записал: редкие фразы'
    res3 = _clean_orchestrator_human_reply(t3)
    assert res3 == "Записал: редкие фразы"

    # 4. Partial choices cut remnant
    t4 = '}],"choices":["Клиповый монтаж с субтитрами"]}\n\nЗаписал: клиповый монтаж'
    res4 = _clean_orchestrator_human_reply(t4)
    assert res4 == "Записал: клиповый монтаж"



def test_briefing_duration_given_as_range_is_saved():
    """Модель отвечает подписью варианта («30–45с», «8-12»): раньше float() падал и длительность терялась."""
    from app.models import Project
    from app.web.routers.db_browser import _apply_set_briefing, _briefing_number

    assert _briefing_number("30–45с") == 37.5
    assert _briefing_number("60 сек") == 60.0
    assert _briefing_number(40) == 40.0
    p = Project(slug="b", topic="t", meta={})
    label = _apply_set_briefing(p, {"duration": "30–45с (Стандарт / 8-12 сцен)", "target_frames": "8-12"})
    assert "duration=37.5s" in label and "frames=10" in label
    assert p.meta["briefing"]["duration_seconds"] == 37.5
    assert p.meta["briefing"]["target_frames"] == 10


def test_briefing_unknown_plan_prompt_falls_back_to_default():
    """«Вирусный Shorts» без своего файла: раньше писалось неизвестное имя и шаг брал чужой глобальный промт."""
    p = Project(slug="v", topic="t", meta={}, prompt_overrides={})
    label = _apply_set_briefing(p, {"plan_prompt": "viral_dynamic_shorts"})
    assert "plan_prompt=default" in label
    assert p.prompt_overrides["plan"] == "default"
    b = p.meta["briefing"]
    assert b["plan_prompt_requested"] == "viral_dynamic_shorts"
    assert b["scenario_type"] == "viral_dynamic_shorts"
    assert "Тип сценария: viral_dynamic_shorts" in build_duration_params_block(p, "plan", header="Сценарий")


def test_reply_drops_cjk_inside_russian_word():
    assert _clean_orchestrator_human_reply("Нужна визуальная一致ность?") == "Нужна визуальная согласованность?"
    assert _clean_orchestrator_human_reply("Стиль 水彩 акварель") == "Стиль  акварель"


def test_briefing_keeps_hero_description():
    """«один герой, рыжий кот в шарфе»: сохраняется сам герой, а не только hero_mode."""
    p = Project(slug="h", topic="t", meta={}, prompt_overrides={})
    label = _apply_set_briefing(p, {"hero_mode": "hero", "hero_description": "рыжий кот в шарфе"})
    assert "hero_description" in label
    assert p.hero_description == "рыжий кот в шарфе"
    assert "Главный герой: рыжий кот в шарфе" in build_duration_params_block(p, "plan", header="Сценарий")
    p2 = Project(slug="h2", topic="t", meta={}, prompt_overrides={}, hero_description="уже описан")
    _apply_set_briefing(p2, {"hero_description": "другой"})
    assert p2.hero_description == "уже описан"
    assert p2.meta["briefing"]["hero_description"] == "другой"


def test_briefing_context_lists_saved_answers_for_final_summary():
    from app.web.routers.db_browser import _briefing_context

    p = Project(slug="b", title="B", meta={}, prompt_overrides={})
    assert _briefing_context(p) == []
    _apply_set_briefing(p, {"duration": 30, "target_frames": 8, "scenario_type": "Документальный",
                            "subtitles_enabled": False})
    lines = _briefing_context(p)
    text = "\n".join(lines)
    assert lines[0].startswith("БРИФИНГ")
    assert "хронометраж, с: 30" in text
    assert "тип сценария: Документальный" in text
    assert "субтитры: нет" in text


def test_prompt_says_briefing_is_saved_at_once():
    from app.web.routers.db_browser import _system_prompt

    sp = _system_prompt(False)
    assert "«Записал" in sp
    assert "ВСЕХ восьми пунктов" in sp
