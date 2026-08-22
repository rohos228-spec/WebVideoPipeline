"""Этап 5, блок B: контракт apply-ops + канонический экстрактор."""

from __future__ import annotations

import json

import pytest

from app.contracts import (
    APPLY_OPS,
    LlmContractError,
    extract_json_payload,
)

OK_REPLY = json.dumps(
    {
        "ops": [
            {
                "frame_uuid": "a1b2",
                "fields": {"Промт Картинки": "закат", "закадр": "текст"},
            }
        ]
    },
    ensure_ascii=False,
)


# ── extract_json_payload ─────────────────────────────────────────────────


def test_extract_plain_json() -> None:
    assert extract_json_payload('{"ops": []}') == {"ops": []}


def test_extract_from_fence_with_prose() -> None:
    text = 'Вот результат:\n```json\n{"ops": [{"a": 1}]}\n```\nГотово!'
    assert extract_json_payload(text) == {"ops": [{"a": 1}]}


def test_extract_balanced_with_brace_in_string() -> None:
    # `{` внутри строки не ломает баланс (баг экстрактора №5 карты §4.3)
    text = 'мусор {"ops": [{"v": "скобка { внутри"}]} хвост'
    assert extract_json_payload(text)["ops"][0]["v"] == "скобка { внутри"


def test_extract_empty_raises_parse() -> None:
    with pytest.raises(LlmContractError) as ei:
        extract_json_payload("   ")
    assert ei.value.kind == "parse"


def test_extract_prose_raises_parse_with_feedback() -> None:
    with pytest.raises(LlmContractError) as ei:
        extract_json_payload("Извините, я не могу выполнить запрос.")
    assert ei.value.kind == "parse"
    assert "JSON" in str(ei.value)


# ── ApplyOpsEnvelope ─────────────────────────────────────────────────────


def test_parse_ok_with_russian_aliases() -> None:
    parsed = APPLY_OPS.parse(OK_REPLY)
    op = parsed.payload.ops[0]
    # «Промт Картинки» → canon (lower + пробел→_), «закадр» → voiceover_text
    assert op.fields == {"image_prompt": "закат", "voiceover_text": "текст"}
    assert parsed.payload.frame_uuids() == ["a1b2"]


def test_unknown_field_is_validate_error_with_name() -> None:
    bad = json.dumps(
        {"ops": [{"frame_uuid": "x", "fields": {"неведомое_поле": 1}}]},
        ensure_ascii=False,
    )
    with pytest.raises(LlmContractError) as ei:
        APPLY_OPS.parse(bad)
    assert ei.value.kind == "validate"
    assert "неведомое_поле" in str(ei.value)


def test_dup_synonyms_last_write_wins() -> None:
    # image_prompt и промт_картинки → один canon; побеждает последний
    raw = '{"ops":[{"frame_uuid":"x","fields":{"image_prompt":"a","промт_картинки":"b"}}]}'
    parsed = APPLY_OPS.parse(raw)
    assert parsed.payload.ops[0].fields == {"image_prompt": "b"}


def test_actions_alias_for_ops() -> None:
    parsed = APPLY_OPS.parse('{"actions":[{"frame_uuid":"x","fields":{"закадр":"т"}}]}')
    assert len(parsed.payload.ops) == 1


def test_empty_ops_rejected() -> None:
    with pytest.raises(LlmContractError) as ei:
        APPLY_OPS.parse('{"ops": []}')
    assert "пустой ops" in str(ei.value)


def test_frame_without_uuid_rejected() -> None:
    with pytest.raises(LlmContractError) as ei:
        APPLY_OPS.parse('{"ops":[{"fields":{"закадр":"т"}}]}')
    assert "frame_uuid" in str(ei.value)


def test_replace_frames_mix_rejected() -> None:
    bad = json.dumps(
        {
            "ops": [
                {"target": "replace_frames", "frames": [{"закадр": "a"}]},
                {"frame_uuid": "x", "fields": {"закадр": "b"}},
            ]
        },
        ensure_ascii=False,
    )
    with pytest.raises(LlmContractError) as ei:
        APPLY_OPS.parse(bad)
    assert "replace_frames" in str(ei.value)


def test_replace_frames_kadry_alias() -> None:
    parsed = APPLY_OPS.parse('{"ops":[{"target":"replace_frames","кадры":[{"закадр":"a"},{"закадр":"b"}]}]}')
    assert len(parsed.payload.ops[0].frames or []) == 2


def test_project_target_uses_project_aliases() -> None:
    parsed = APPLY_OPS.parse('{"ops":[{"target":"project","fields":{"общий_план":"план"}}]}')
    assert parsed.payload.ops[0].fields == {"general_plan": "план"}


def test_salvaged_marker_moved_to_meta() -> None:
    raw = json.dumps(
        {
            "_salvaged_partial": True,
            "ops": [{"frame_uuid": "x", "fields": {"закадр": "т"}}],
        },
        ensure_ascii=False,
    )
    parsed = APPLY_OPS.parse(raw)
    assert parsed.meta == {"_salvaged_partial": True}


def test_extra_top_level_key_rejected() -> None:
    with pytest.raises(LlmContractError):
        APPLY_OPS.parse('{"ops":[{"frame_uuid":"x","fields":{"закадр":"т"}}],"comment":"…"}')


# ── паритет с normalize_fields (единый источник алиасов) ─────────────────


def test_alias_parity_with_normalize_fields() -> None:
    from app.services.db_apply import FIELD_ALIASES, normalize_fields

    sample = {alias: "v" for alias in list(FIELD_ALIASES)[:40]}
    legacy = normalize_fields(dict(sample), FIELD_ALIASES, scope="t")
    parsed = APPLY_OPS.parse(json.dumps({"ops": [{"frame_uuid": "x", "fields": sample}]}, ensure_ascii=False))
    assert parsed.payload.ops[0].fields == legacy


def test_response_schema_not_strict() -> None:
    rs = APPLY_OPS.response_schema()
    assert rs.name == "vp_apply_ops"
    assert rs.strict is False
    assert rs.schema["type"] == "object"
