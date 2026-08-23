"""Этап 5, B.4: узкие контракты — split, img_pr, anim_pr, voiceover."""

from __future__ import annotations

import json

import pytest

from app.contracts import (
    ANIM_PR,
    FRAME_SPLIT,
    IMG_PR,
    VOICEOVER,
    LlmContractError,
)

# Промт короче MIN_IMAGE_PROMPT_CHARS контракт теперь отбивает как заглушку
# (живой прогон: кадр с промтом «...» доехал до генератора). Тесты ниже про
# алиасы и null-поля, не про длину, — тела добиты до правдоподобных.
_PROMPT_BODY = "закат над вагоном, " + "иней на стекле, тень на полу, " * 15


# ── FRAME_SPLIT ──────────────────────────────────────────────────────────


def _split_reply(frames: list[dict]) -> str:
    return json.dumps(
        {"ops": [{"target": "replace_frames", "frames": frames}]},
        ensure_ascii=False,
    )


def test_split_ok_with_aliases() -> None:
    parsed = FRAME_SPLIT.parse(
        _split_reply(
            [
                {"закадр": "первый", "длительность": 4.5, "смысл": "интро"},
                {"voiceover_text": "второй"},
            ]
        )
    )
    frames = parsed.payload.frames
    assert frames[0].voiceover_text == "первый"
    assert frames[0].duration_seconds == 4.5
    assert frames[0].meaning == "интро"
    assert frames[1].voiceover_text == "второй"


def test_split_one_frame_rejected() -> None:
    with pytest.raises(LlmContractError) as ei:
        FRAME_SPLIT.parse(_split_reply([{"закадр": "один"}]))
    assert "≥2" in str(ei.value)


def test_split_empty_voiceover_rejected() -> None:
    with pytest.raises(LlmContractError):
        FRAME_SPLIT.parse(_split_reply([{"закадр": "ок"}, {"закадр": "  "}]))


def test_split_two_ops_rejected() -> None:
    raw = json.dumps(
        {
            "ops": [
                {"target": "replace_frames", "frames": [{"закадр": "a"}, {"закадр": "b"}]},
                {"target": "replace_frames", "frames": [{"закадр": "c"}, {"закадр": "d"}]},
            ]
        },
        ensure_ascii=False,
    )
    with pytest.raises(LlmContractError) as ei:
        FRAME_SPLIT.parse(raw)
    assert "ровно одна" in str(ei.value)


def test_split_kadry_alias() -> None:
    raw = json.dumps(
        {"ops": [{"target": "replace_frames", "кадры": [{"закадр": "a"}, {"закадр": "b"}]}]},
        ensure_ascii=False,
    )
    assert len(FRAME_SPLIT.parse(raw).payload.frames) == 2


# ── IMG_PR ───────────────────────────────────────────────────────────────


def test_img_pr_ok_russian_alias_and_characters() -> None:
    raw = json.dumps(
        {
            "ops": [
                {
                    "frame_uuid": "u1",
                    "fields": {"промт_картинки": _PROMPT_BODY, "персонажи": "c01"},
                }
            ]
        },
        ensure_ascii=False,
    )
    op = IMG_PR.parse(raw).payload.ops[0]
    assert op.fields == {"image_prompt": _PROMPT_BODY, "characters": "c01"}


def test_img_pr_null_characters_dropped() -> None:
    raw = json.dumps(
        {"ops": [{"frame_uuid": "u1", "fields": {"image_prompt": _PROMPT_BODY, "characters": None}}]},
        ensure_ascii=False,
    )
    op = IMG_PR.parse(raw).payload.ops[0]
    assert op.fields == {"image_prompt": _PROMPT_BODY}


def test_img_pr_foreign_field_rejected() -> None:
    raw = '{"ops":[{"frame_uuid":"u1","fields":{"image_prompt":"x","закадр":"y"}}]}'
    with pytest.raises(LlmContractError) as ei:
        IMG_PR.parse(raw)
    assert "voiceover_text" in str(ei.value)


def test_img_pr_missing_prompt_rejected() -> None:
    raw = '{"ops":[{"frame_uuid":"u1","fields":{"персонажи":"c01"}}]}'
    with pytest.raises(LlmContractError):
        IMG_PR.parse(raw)


def test_img_pr_strict_schema_override() -> None:
    rs = IMG_PR.response_schema()
    assert rs.strict is True
    assert rs.schema["additionalProperties"] is False
    item = rs.schema["properties"]["ops"]["items"]
    assert set(item["required"]) == {"frame_uuid", "fields"}
    # Панель: strict-схема обязана уметь shot2 (иначе enforced-релей
    # физически не вернёт промт второго шота — тихая потеря)
    fields = item["properties"]["fields"]
    assert "image_prompt_shot2" in fields["properties"]
    assert set(fields["required"]) == set(fields["properties"])


def test_img_pr_shot2_only_op_with_nulls() -> None:
    # Ответ enforced-релея: неиспользуемые поля = null (required-all)
    raw = json.dumps(
        {
            "ops": [
                {
                    "frame_uuid": "u1",
                    "fields": {
                        "image_prompt": None,
                        "image_prompt_shot2": _PROMPT_BODY,
                        "characters": None,
                    },
                }
            ]
        },
        ensure_ascii=False,
    )
    op = IMG_PR.parse(raw).payload.ops[0]
    assert op.fields == {"image_prompt_shot2": _PROMPT_BODY}


def test_anim_pr_strict_schema_has_shot2() -> None:
    rs = ANIM_PR.response_schema()
    fields = rs.schema["properties"]["ops"]["items"]["properties"]["fields"]
    assert "animation_prompt_shot2" in fields["properties"]


# ── ANIM_PR ──────────────────────────────────────────────────────────────


def test_anim_pr_ok() -> None:
    raw = '{"ops":[{"frame_uuid":"u1","fields":{"промт_видео":"наезд камеры"}}]}'
    op = ANIM_PR.parse(raw).payload.ops[0]
    assert op.fields == {"animation_prompt": "наезд камеры"}


def test_anim_pr_image_prompt_rejected() -> None:
    raw = '{"ops":[{"frame_uuid":"u1","fields":{"image_prompt":"x"}}]}'
    with pytest.raises(LlmContractError):
        ANIM_PR.parse(raw)


# ── VOICEOVER ────────────────────────────────────────────────────────────


def test_voiceover_frame_ok() -> None:
    raw = '{"ops":[{"frame_uuid":"u1","fields":{"закадр":"текст"}}]}'
    op = VOICEOVER.parse(raw).payload.ops[0]
    assert op.fields == {"voiceover_text": "текст"}


def test_voiceover_project_script_ok() -> None:
    raw = '{"ops":[{"target":"project","fields":{"закадровый_текст":"весь текст"}}]}'
    op = VOICEOVER.parse(raw).payload.ops[0]
    assert op.fields == {"script_text": "весь текст"}


def test_voiceover_foreign_field_rejected() -> None:
    raw = '{"ops":[{"frame_uuid":"u1","fields":{"закадр":"т","image_prompt":"x"}}]}'
    with pytest.raises(LlmContractError):
        VOICEOVER.parse(raw)


# ── реестр ───────────────────────────────────────────────────────────────


def test_registry_has_all() -> None:
    from app.contracts import get_contract

    for name in ("vp_apply_ops", "vp_frame_split", "vp_img_pr", "vp_anim_pr", "vp_voiceover"):
        assert get_contract(name).name == name
