"""Шоты многокадровой сцены получают промт картинки наравне с VO-кадрами.

``camera_expand`` разворачивает сцену в несколько шотов и оставляет закадр
только родителю — дети по устройству пустые. Отбор «есть закадр» пропускал
их молча, и многокадровая сцена схлопывалась обратно в один кадр на ячейку.
"""

from __future__ import annotations

from app.models import Frame
from app.orchestrator.steps.generate_image_prompts import (
    _frames_needing_image_prompt,
    _frames_with_image_prompt,
    _is_real_shot,
)


def _parent(number: int, prompt: str = "") -> Frame:
    return Frame(
        project_id=1,
        number=number,
        voiceover_text="Игнат едет в последнем вагоне.",
        image_prompt=prompt,
        attrs={"camera_subdivide": {"parent_uuid": "u1", "shot_index": 1, "role": "vo_parent"}},
    )


def _child(number: int, prompt: str = "") -> Frame:
    return Frame(
        project_id=1,
        number=number,
        voiceover_text="",
        image_prompt=prompt,
        attrs={"camera_subdivide": {"parent_uuid": "u1", "shot_index": 2, "role": "shot"}},
    )


def _orphan(number: int) -> Frame:
    """Пустой кадр без разметки камеры — не шот, картинку ему рисовать не из чего."""
    return Frame(project_id=1, number=number, voiceover_text="", image_prompt="", attrs={})


def test_shot_child_is_a_real_shot() -> None:
    assert _is_real_shot(_parent(1)) is True
    assert _is_real_shot(_child(2)) is True
    assert _is_real_shot(_orphan(3)) is False


def test_children_are_queued_for_prompts() -> None:
    frames = [_parent(1), _child(2), _child(3), _orphan(4)]
    pending = _frames_needing_image_prompt(frames)
    assert [fr.number for fr in pending] == [1, 2, 3]


def test_children_count_as_done_when_filled() -> None:
    frames = [_parent(1, "промт"), _child(2, "промт"), _child(3)]
    assert [fr.number for fr in _frames_with_image_prompt(frames)] == [1, 2]
    assert [fr.number for fr in _frames_needing_image_prompt(frames)] == [3]


def test_both_filters_agree_on_what_a_shot_is() -> None:
    """Отбор шага и отбор батчей обязаны совпадать, иначе шаг зациклится.

    Шаг считает готовность своим фильтром, а батчи собирает загрузчик
    контекста своим. Разойдутся — шаг вечно видит «не хватает кадров»,
    а посылать в модель нечего.
    """
    from app.services.xlsx_step_runners import _is_real_shot as batch_is_real_shot

    cases = [_parent(1), _child(2), _orphan(3)]
    assert [_is_real_shot(fr) for fr in cases] == [batch_is_real_shot(fr) for fr in cases]
