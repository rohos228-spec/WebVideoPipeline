"""Unit tests for Montage Board AI Agent fixes and Prompt Sanitizer:

1. Prompt Sanitizer (clean_prompt_from_ai_chatter):
   - Refusal tail stripping (as in user screenshot)
   - Full refusal detection
   - Leading/trailing chatter stripping
2. Shot 2 action extraction (_action_for_frame) and DB card pinning (_pin_ai_change_shot_action)
3. Shot 2 image references (_montage_shot2_refs)
4. Video master prompt loading (load_anim_pr_master)
5. Tenant preservation in spawn_apply_job
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.services.montage_ai_change import (
    _pin_ai_change_shot_action,
    system_for_kind,
)
from app.services.montage_board import _action_for_frame
from app.services.montage_board_regen import _montage_shot2_refs
from app.services.prompt_sanitizer import (
    clean_prompt_from_ai_chatter,
)


def test_prompt_sanitizer_screenshot_case():
    raw = (
        "evidence markers, warm desk lamp light, true-crime research wall. "
        "Не цифровой интерфейс, не чистая иллюстрация, не пустой фон. — "
        "I не могу выполнить эту генерацию. Запрос описывает вооружённого преступника "
        "как главный визуальный объект в реалистичной постановочной сцене "
        "(детективная доска с фото, улики, освещение) — это по сути создание "
        "правдоподобного изображения человека с оружием в криминальном контексте, "
        "что слишком близко к контенту, запрещённому правилами."
    )
    cleaned, refused = clean_prompt_from_ai_chatter(raw)
    assert not refused
    assert cleaned == (
        "evidence markers, warm desk lamp light, true-crime research wall. "
        "Не цифровой интерфейс, не чистая иллюстрация, не пустой фон."
    )
    assert "не могу выполнить" not in cleaned
    assert "вооружённого преступника" not in cleaned


def test_prompt_sanitizer_full_refusal():
    refusals = [
        "Я не могу выполнить данный запрос, так как он нарушает политику безопасности.",
        "I cannot fulfill this request because it violates safety guidelines.",
        "К сожалению, я не могу сгенерировать данное изображение.",
        "Извините, но я не могу создать сцену с насилием.",
        "As an AI, I am unable to generate images of weapons.",
    ]
    for r in refusals:
        cleaned, refused = clean_prompt_from_ai_chatter(r)
        assert refused, f"Expected refused=True for: {r}"
        assert cleaned == ""


def test_prompt_sanitizer_intros_and_trailing_notes():
    text = (
        "Вот обновленный промпт: cinematic detective room, cold lighting.\n\n"
        "Примечание: триггерные слова заменены на нейтральные."
    )
    cleaned, refused = clean_prompt_from_ai_chatter(text)
    assert not refused
    assert cleaned == "cinematic detective room, cold lighting."

    text2 = "Here is the prompt: vintage library, dusty books, warm sunlight."
    cleaned2, refused2 = clean_prompt_from_ai_chatter(text2)
    assert not refused2
    assert cleaned2 == "vintage library, dusty books, warm sunlight."


def test_action_for_frame_shot1_and_shot2():
    frame = SimpleNamespace(
        attrs={
            "shot01_action": "Детектив входит в комнату и осматривается",
            "shot02_action": "Крупный план: рука детектива берет лупу со стола",
        }
    )
    act1 = _action_for_frame(frame, shot=1)
    act2 = _action_for_frame(frame, shot=2)
    assert act1 == "Детектив входит в комнату и осматривается"
    assert act2 == "Крупный план: рука детектива берет лупу со стола"


def test_action_for_frame_shot2_fallback_to_kadry():
    frame = SimpleNamespace(
        attrs={
            "shot01_action": "Первый шот действие",
            "кадры": [
                {"id": "K01", "действие": "Первый шот из кадров"},
                {"id": "K01-s2", "действие": "Второй шот из кадров: осмотр улик"},
            ],
        }
    )
    act2 = _action_for_frame(frame, shot=2)
    assert act2 == "Второй шот из кадров: осмотр улик"


def test_pin_ai_change_shot_action_shot2():
    frame = SimpleNamespace(
        attrs={
            "shot01_action": "Шот 1 экшен",
            "shot02_action": "Шот 2 экшен",
            "кадры": [
                {"id": "T5-K0", "действие": "Шот 1 экшен"},
                {"id": "T5-K0-s2", "действие": "Шот 2 экшен"},
            ],
        }
    )
    ctx = {
        "frames": [
            {
                "shot01_action": "Старый",
                "кадры": [
                    {"id": "T5-K0", "действие": "Шот 1"},
                    {"id": "T5-K0-s2", "действие": "Шот 2"},
                ],
            }
        ]
    }
    _pin_ai_change_shot_action(ctx, frame, shot=2)
    row = ctx["frames"][0]
    assert row["shot02_action"] == "Шот 2 экшен"
    assert len(row["кадры"]) == 1
    assert row["кадры"][0]["id"] == "T5-K0-s2"


def test_system_for_kind():
    img_sys = system_for_kind("image")
    assert "агент из вложенного файла" in img_sys
    assert "Категорически запрещено" in img_sys

    vid_sys = system_for_kind("video")
    assert "Ты — агент анимации и видеопромптов" in vid_sys
    assert "silent video only" in vid_sys.lower()
    assert "Категорически запрещено" in vid_sys


@pytest.mark.asyncio
async def test_montage_shot2_refs_uses_shot1_image(tmp_path):
    scenes_dir = tmp_path / "scenes"
    scenes_dir.mkdir()
    shot1_file = scenes_dir / "frame_005_a1b2c3d4.png"
    shot1_file.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 200)

    project = SimpleNamespace(id=1, data_dir=tmp_path)
    frame = SimpleNamespace(id=10, number=5, attrs={})

    session = AsyncMock()
    refs = await _montage_shot2_refs(session, project, frame, scenes_dir=scenes_dir)
    assert len(refs) == 1
    assert refs[0] == shot1_file


@pytest.mark.asyncio
async def test_spawn_apply_job_preserves_tenant():
    from app.services.montage_board_apply_job import spawn_apply_job
    from app.services.tenant import current_tenant, tenant_scope

    observed_tenant = None

    async def fake_apply(session, project, **kwargs):
        nonlocal observed_tenant
        observed_tenant = current_tenant()
        return {"ok": True, "results": []}

    fake_project = SimpleNamespace(id=999, meta={})

    mock_session = AsyncMock()
    mock_session.get.return_value = fake_project

    class FakeSessionScope:
        async def __aenter__(self):
            return mock_session

        async def __aexit__(self, *args):
            pass

    test_tenant_id = "12345678-1234-5678-1234-567812345678"
    with (
        tenant_scope(test_tenant_id),
        patch("app.services.montage_board_apply_job.session_scope", return_value=FakeSessionScope()),
        patch("app.services.montage_board_apply_job.apply_montage_board", side_effect=fake_apply),
        patch("app.services.montage_board_apply_job._publish", new_callable=AsyncMock),
    ):
        task = spawn_apply_job(
            999, video_trims=None, pending_ops=[{"type": "image_regen", "frame_number": 1, "shot": 1}]
        )
        await task

    assert observed_tenant == test_tenant_id
