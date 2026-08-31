"""Обрыв ответа чанка отличается от результата.

Живой прогон 2026-08-31: у чанков `validate=False` (валидируется только
склейка), поэтому кусок, вернувший 2 сцены на 10 кадров, доезжал до
`merge_agent_slices` как валидный. Кадры терялись молча, а падало это
через пять минут в другом месте — «биты скелета без фазы».
"""

from __future__ import annotations

import pytest

from app.services.scene_design.agent_chunks import (
    ShortChunkAnswer,
    is_capacity_failure,
    short_chunk_problem,
)


def test_short_answer_detected() -> None:
    data = {"scenes": [{"id_scene": "scene_01"}, {"id_scene": "scene_02"}]}
    problem = short_chunk_problem("action", data, frames_in_chunk=10, label="p2/3")
    assert problem is not None
    assert "p2/3" in problem and "10" in problem


def test_full_answer_passes() -> None:
    data = {"scenes": [{"id_scene": f"scene_{i:02d}"} for i in range(1, 11)]}
    assert short_chunk_problem("action", data, frames_in_chunk=10, label="p1/3") is None


def test_half_is_the_border() -> None:
    """Порог грубый: ровно половина — ещё не обрыв, меньше половины — обрыв."""
    half = {"scenes": [{"id_scene": f"scene_{i}"} for i in range(5)]}
    assert short_chunk_problem("action", half, frames_in_chunk=10, label="p") is None
    less = {"scenes": [{"id_scene": f"scene_{i}"} for i in range(4)]}
    assert short_chunk_problem("action", less, frames_in_chunk=10, label="p") is not None


def test_camera_counted_by_its_own_key() -> None:
    data = {"shots": [{"id_shot": "shot_01"}]}
    assert short_chunk_problem("camera", data, frames_in_chunk=8, label="p") is not None


@pytest.mark.parametrize("agent", ["skeleton", "characters", "world"])
def test_non_splittable_agents_not_checked(agent: str) -> None:
    """Скелет и паспорта дробить нечего — проверка их не касается."""
    assert short_chunk_problem(agent, {"scenes": []}, frames_in_chunk=10, label="p") is None


def test_tiny_chunk_not_checked() -> None:
    """На 1-3 кадрах доля ничего не значит — не дёргаем split на шуме."""
    assert short_chunk_problem("action", {"scenes": []}, frames_in_chunk=3, label="p") is None


def test_garbage_payload_counts_as_short() -> None:
    assert short_chunk_problem("action", None, frames_in_chunk=10, label="p") is not None
    assert short_chunk_problem("action", {"scenes": "не список"}, frames_in_chunk=10, label="p") is not None


def test_short_answer_routes_to_split_not_to_plain_retry() -> None:
    """Не влезло в ответ — дроби кусок; это capacity-семейство."""
    assert is_capacity_failure(ShortChunkAnswer("scene_design/action: кусок p2/3 вернул 2"))
