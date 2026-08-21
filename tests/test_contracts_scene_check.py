"""Этап 5, B.5-B.6: контракты scene_design-комплекса и CheckReport."""

from __future__ import annotations

import json

import pytest

from app.contracts import (
    CHECK_REPORT,
    SD_ASSEMBLE,
    SD_CAMERA,
    SD_CHARACTERS,
    SD_SKELETON,
    SD_WORLD,
    SLICE_CONTRACTS,
    LlmContractError,
)

# ── scene_design срезы ───────────────────────────────────────────────────


def test_skeleton_scenes_ok() -> None:
    p = SD_SKELETON.parse('{"scenes":[{"id":"scene_01"}],"extra":"ok"}')
    assert p.payload.scenes == [{"id": "scene_01"}]


def test_skeleton_cells_ok() -> None:
    p = SD_SKELETON.parse('{"cells":[{"vo":"текст"}]}')
    assert p.payload.cells


def test_skeleton_legacy_russian_scenes_key() -> None:
    p = SD_SKELETON.parse(json.dumps({"сцены": [{"id": 1}]}, ensure_ascii=False))
    assert p.payload.scenes == [{"id": 1}]


def test_skeleton_empty_rejected() -> None:
    with pytest.raises(LlmContractError):
        SD_SKELETON.parse('{"scenes":[]}')


def test_agent_error_key_rejected() -> None:
    with pytest.raises(LlmContractError) as ei:
        SD_CHARACTERS.parse(
            json.dumps(
                {"characters": [1], "error": "не хватает контекста"},
                ensure_ascii=False,
            )
        )
    assert "не хватает контекста" in str(ei.value)


def test_characters_non_empty_required() -> None:
    with pytest.raises(LlmContractError):
        SD_CHARACTERS.parse('{"characters":[]}')


def test_world_empty_locations_ok() -> None:
    assert SD_WORLD.parse('{"locations":[]}').payload.locations == []


def test_camera_shot_plan_required() -> None:
    with pytest.raises(LlmContractError):
        SD_CAMERA.parse('{"scenes":[1]}')


def test_assemble_ok_and_report_optional() -> None:
    raw = json.dumps(
        {
            "characters": [],
            "scenes": [{"id": "scene_01"}],
            "ops": [{"frame_uuid": "u1", "fields": {"закадр": "т"}}],
        },
        ensure_ascii=False,
    )
    p = SD_ASSEMBLE.parse(raw)
    assert p.payload.report is None
    assert len(p.payload.ops) == 1


def test_assemble_empty_ops_rejected() -> None:
    with pytest.raises(LlmContractError):
        SD_ASSEMBLE.parse('{"characters":[],"scenes":[{"a":1}],"ops":[]}')


def test_slice_contracts_map_complete() -> None:
    assert set(SLICE_CONTRACTS) == {
        "skeleton",
        "characters",
        "world",
        "camera",
        "action",
        "assemble",
    }


# ── CheckReport (vp.check.v1) ────────────────────────────────────────────

OK_CHECK = json.dumps(
    {
        "schema": "vp.check.v1",
        "verdict": "pass",
        "summary": "всё ок",
        "checks": [{"id": "plan", "ok": True, "note": ""}],
        "forward": {"mode": "inherit", "paths": []},
        "fix": {"target": "none", "instructions": "", "rewrite_file": None},
    },
    ensure_ascii=False,
)


def test_check_report_ok() -> None:
    p = CHECK_REPORT.parse(OK_CHECK)
    assert p.payload.verdict == "pass"
    assert p.payload.fix.target == "none"


def test_check_report_decision_synonym() -> None:
    p = CHECK_REPORT.parse('{"decision":"approved","summary":"x"}')
    assert p.payload.verdict == "pass"
    p = CHECK_REPORT.parse('{"decision":"regen"}')
    assert p.payload.verdict == "fail"


def test_check_report_no_verdict_is_contract_error() -> None:
    # Спека «Битый ответ проверки ≠ вердикт fail»: эхо apply-ops → ошибка
    with pytest.raises(LlmContractError) as ei:
        CHECK_REPORT.parse('{"ops":[{"frame_uuid":"u1"}]}')
    assert "verdict" in str(ei.value)


def test_check_report_wrong_schema_rejected() -> None:
    with pytest.raises(LlmContractError):
        CHECK_REPORT.parse('{"schema":"vp.other.v9","verdict":"pass"}')


def test_check_report_garbage_is_parse_error() -> None:
    with pytest.raises(LlmContractError) as ei:
        CHECK_REPORT.parse("Извините, файл не открылся.")
    assert ei.value.kind == "parse"
