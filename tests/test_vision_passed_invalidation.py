"""Тест C.6a: инвалидация кадра снимает его vision-passed токены."""

from __future__ import annotations

from types import SimpleNamespace

from app.services.vision_check_loop import (
    META_PASSED,
    drop_vision_passed_for_frame,
    get_vision_passed,
)


def _project(passed):
    p = SimpleNamespace(id=1, meta={META_PASSED: list(passed)})
    return p


def test_drop_removes_frame_and_shot_tokens():
    p = _project(["f3", "f3s2", "f31", "c01", "f7"])
    assert drop_vision_passed_for_frame(p, 3) is True
    left = get_vision_passed(p)
    # f31 — другой кадр, не префикс-жертва; c01 — герой, не трогаем.
    assert left == {"f31", "c01", "f7"}


def test_drop_noop_when_frame_absent():
    p = _project(["c01", "f7"])
    assert drop_vision_passed_for_frame(p, 3) is False
    assert get_vision_passed(p) == {"c01", "f7"}
