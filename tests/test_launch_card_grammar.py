"""Согласование чисел в текстах карточки запуска очереди: один проект, 2-4, 5+, группы причин."""

from __future__ import annotations

import pytest

from app.orchestrator.agents import supervisor as sup
from app.orchestrator.agents.approval_steps import slots_text
from app.services.action_layer.builtin_launch import _queue_effects
from app.services.action_layer.planner_rules import _param_text

_SCENE = "кадры без данных сцены, сначала запустите ноду «Работа с GPT 2»."
_HEAD = ("Не поставлен в очередь", "Не поставлены в очередь")
_GOES = ("ставится в очередь как обычно", "ставятся в очередь как обычно")


def test_single_project_param_is_singular() -> None:
    assert _param_text("project_ids", [11], None) == "проект 11"
    assert _param_text("project_ids", [11, 12], None) == "проекты 11, 12"


@pytest.mark.parametrize(
    ("n", "head"),
    [
        (1, "1 проект встанет в очередь генерации: воркер сам пройдёт конвейер в нём "),
        (2, "2 проекта встанут в очередь генерации: воркер сам пройдёт конвейер в каждом "),
        (5, "5 проектов встанут в очередь генерации: воркер сам пройдёт конвейер в каждом "),
        (11, "11 проектов встанут в очередь генерации"),
        (21, "21 проект встанет в очередь генерации: воркер сам пройдёт конвейер в каждом "),
    ],
)
def test_queue_effects_agreement(n: int, head: str) -> None:
    text = _queue_effects({"project_ids": list(range(1, n + 1))})
    assert text.startswith(head), text
    if n == 1:
        assert "у этого проекта" in text


def test_one_kept_project_named() -> None:
    text = sup._blocked_launch_text(_HEAD, [(10, _SCENE)], [11], _GOES)
    assert text.startswith(f"Не поставлен в очередь: #10 — {_SCENE.rstrip('.')}. "), text
    assert "Проект #11 ставится в очередь как обычно." in text
    assert "Остальной" not in text


def test_same_reasons_grouped() -> None:
    text = sup._blocked_launch_text(_HEAD, [(10, _SCENE), (12, _SCENE)], [3, 4, 5, 6, 7, 8, 9, 11], _GOES)
    assert text.startswith(
        "Не поставлены в очередь: #10, #12 — кадры без данных сцены, сначала "
        "запустите ноду «Работа с GPT 2». Остальные 8 проектов ставятся в очередь "
        "как обычно."
    ), text
    assert "«запусти проекты 10, 12»" in text


def test_different_reasons_kept_apart() -> None:
    text = sup._blocked_launch_text(
        _HEAD, [(10, _SCENE), (12, "нет автопродвижения"), (13, _SCENE)], [3, 4], _GOES
    )
    assert "#10, #13 — кадры без данных сцены" in text
    assert "#12 — нет автопродвижения" in text
    assert "Остальные 2 проекта ставятся" in text


def test_slots_text_singular() -> None:
    plan = {"count": 3, "max_parallel": 2, "busy": 0, "start_now": 2, "waiting": 1}
    assert "2 начнут сразу, 1 встанет в очередь и начнёт" in slots_text(plan)
    plan = {"count": 1, "max_parallel": 1, "busy": 1, "start_now": 0, "waiting": 1}
    assert "проект встанет в очередь и начнёт" in slots_text(plan)
