"""Test img_pr_batch_size configuration."""

from __future__ import annotations

from types import SimpleNamespace

from app.services.xlsx_step_runners import (
    DEFAULT_IMG_PR_BATCH_SIZE,
    MAX_IMG_PR_BATCH_SIZE,
    MIN_IMG_PR_BATCH_SIZE,
    img_pr_batch_size,
)


def test_img_pr_batch_size_defaults_and_clamping() -> None:
    assert DEFAULT_IMG_PR_BATCH_SIZE == 7
    assert MIN_IMG_PR_BATCH_SIZE == 2
    assert MAX_IMG_PR_BATCH_SIZE == 8

    # Default when None or empty meta
    assert img_pr_batch_size(None) == 7
    p_empty = SimpleNamespace(meta={})
    assert img_pr_batch_size(p_empty) == 7

    # Explicit valid values
    for val in (2, 3, 4, 5, 6, 7, 8):
        p = SimpleNamespace(meta={"img_pr_batch_size": val})
        assert img_pr_batch_size(p) == val

    # Clamping below minimum
    p_low = SimpleNamespace(meta={"img_pr_batch_size": 1})
    assert img_pr_batch_size(p_low) == 2
    p_zero = SimpleNamespace(meta={"img_pr_batch_size": 0})
    assert img_pr_batch_size(p_zero) == 2

    # Clamping above maximum
    p_high = SimpleNamespace(meta={"img_pr_batch_size": 9})
    assert img_pr_batch_size(p_high) == 8
    p_very_high = SimpleNamespace(meta={"img_pr_batch_size": 50})
    assert img_pr_batch_size(p_very_high) == 8

    # Invalid string / type fallback
    p_bad = SimpleNamespace(meta={"img_pr_batch_size": "invalid"})
    assert img_pr_batch_size(p_bad) == 7
