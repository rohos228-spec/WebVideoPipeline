"""Тесты хэш-модуля этапа 2 (A.3): стабильность и чувствительность."""

from __future__ import annotations

import pytest

from app.services.input_hash import (
    HASH_VERSION,
    canonical_json,
    compute_input_hash,
    contract_fingerprint,
    file_content_hash,
    hashes_match,
    media_fingerprint,
    normalize_text,
    prompt_version_hash,
)


def _base_hash(**overrides):
    kwargs = dict(
        unit_input={"frames": [{"uuid": "a", "text": "t1"}, {"uuid": "b", "text": "t2"}]},
        fingerprint="vp_apply_ops:deadbeef",
        prompt_hash="p" * 64,
        model="gpt-5.6-sol",
        params={"temperature": 0},
    )
    kwargs.update(overrides)
    return compute_input_hash(**kwargs)


# ── стабильность ──────────────────────────────────────────────────────────


def test_dict_key_order_does_not_matter():
    a = compute_input_hash(
        unit_input={"x": 1, "y": 2}, fingerprint="f", params={"a": 1, "b": 2}
    )
    b = compute_input_hash(
        unit_input={"y": 2, "x": 1}, fingerprint="f", params={"b": 2, "a": 1}
    )
    assert a == b


def test_canonical_json_no_whitespace_variation():
    assert canonical_json({"a": [1, 2]}) == '{"a":[1,2]}'


def test_repeated_call_is_stable():
    assert _base_hash() == _base_hash()


def test_hash_carries_version_prefix():
    assert _base_hash().startswith(HASH_VERSION + ":")


# ── чувствительность к каждому компоненту ─────────────────────────────────


@pytest.mark.parametrize(
    "override",
    [
        {"unit_input": {"frames": [{"uuid": "a", "text": "ДРУГОЙ"}]}},
        {"fingerprint": "vp_apply_ops:0000beef"},
        {"prompt_hash": "q" * 64},
        {"model": "gpt-4o"},
        {"params": {"temperature": 1}},
    ],
)
def test_each_component_changes_hash(override):
    assert _base_hash() != _base_hash(**override)


def test_list_order_is_significant():
    # Порядок списков значим — сортировка на совести call-site (allowlist).
    a = _base_hash(unit_input={"frames": ["a", "b"]})
    b = _base_hash(unit_input={"frames": ["b", "a"]})
    assert a != b


# ── нормализация текста / промпта ─────────────────────────────────────────


def test_normalize_text_crlf_and_bom():
    assert normalize_text("﻿a\r\nb\rc") == "a\nb\nc"


def test_prompt_hash_ignores_line_endings():
    assert prompt_version_hash("a\r\nb") == prompt_version_hash("a\nb")


def test_prompt_hash_sensitive_to_text_and_hints():
    base = prompt_version_hash("prompt", hints=["hint1"])
    assert base != prompt_version_hash("prompt2", hints=["hint1"])
    assert base != prompt_version_hash("prompt", hints=["hint2"])
    assert base != prompt_version_hash("prompt")


def test_prompt_hint_boundary_not_concatenation():
    # ("ab", ["c"]) и ("a", ["bc"]) не должны совпасть.
    assert prompt_version_hash("ab", hints=["c"]) != prompt_version_hash(
        "a", hints=["bc"]
    )


# ── отпечатки ─────────────────────────────────────────────────────────────


def test_contract_fingerprint_from_registry():
    fp = contract_fingerprint("vp_apply_ops")
    assert fp.startswith("vp_apply_ops:")
    assert fp == contract_fingerprint("vp_apply_ops")


def test_contract_fingerprint_unknown_raises():
    with pytest.raises(KeyError):
        contract_fingerprint("no-such-contract")


def test_media_fingerprint():
    assert media_fingerprint("outsee", "img") != media_fingerprint("grsai", "img")


def test_file_content_hash_ignores_mtime(tmp_path):
    p = tmp_path / "ref.png"
    p.write_bytes(b"pixels")
    h1 = file_content_hash(p)
    import os

    os.utime(p, (0, 0))  # touch: mtime меняется, содержимое нет
    assert file_content_hash(p) == h1
    p.write_bytes(b"other")
    assert file_content_hash(p) != h1


# ── сверка ────────────────────────────────────────────────────────────────


def test_hashes_match_legacy_checkpoint_is_mismatch():
    current = _base_hash()
    assert hashes_match(current, current)
    assert not hashes_match(None, current)  # legacy без hash
    assert not hashes_match("", current)
    assert not hashes_match(_base_hash(model="x"), current)
