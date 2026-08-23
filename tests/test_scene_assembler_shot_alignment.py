"""Шот подбирается кадру внутри его сцены, а не по позиции в общем списке.

``camera_expand`` разворачивает лестницу крупностей: на 24 кадра приходит 32
строки shot_plan. Позиционный zip после первого расхождения сдвигал всё до
конца ролика — в кадре оказывались описание одного шота и персонажи другого.
"""

from __future__ import annotations

from app.services.scene_design.assembler import build_local_assembler_payload


class _Frame:
    def __init__(self, uuid: str, number: int) -> None:
        self.uuid = uuid
        self.number = number
        self.voiceover_text = ""
        self.attrs: dict = {}
        self.duration_seconds = 3.0


def _shot(sid: str, phase: int, who: str, composition: str) -> dict:
    return {
        "id_scene": sid,
        "phase_index": phase,
        "кто_в_кадре": who,
        "композиция": composition,
        "крупность": "средний план",
    }


def test_frame_takes_the_shot_of_its_own_scene() -> None:
    # sc01 получила лишний шот от лестницы крупностей — кадров там всё равно 2.
    shots = [
        _shot("sc01", 1, "c01", "Игнат садится"),
        _shot("sc01", 2, "c01", "Игнат держит конверт"),
        _shot("sc01", 3, "c01", "пальцы на сгибе бумаги"),
        _shot("sc02", 1, "c02", "руки женщины на коленях"),
        _shot("sc02", 2, "c01", "Игнат опускает глаза"),
    ]
    chrono = [
        {"id_scene": "sc01", "кадры": [{"uuid": "u1"}, {"uuid": "u2"}]},
        {"id_scene": "sc02", "кадры": [{"uuid": "u3"}, {"uuid": "u4"}]},
    ]
    frames = [_Frame("u1", 1), _Frame("u2", 2), _Frame("u3", 3), _Frame("u4", 4)]

    payload = build_local_assembler_payload(
        {"characters": [], "shot_plan_chrono": shots, "scenes_chrono": chrono}, frames
    )
    by_uuid = {op["frame_uuid"]: op["fields"] for op in payload["ops"]}

    # Кадр 3 — первый кадр sc02, а не четвёртая строка общего списка.
    assert "руки женщины" in by_uuid["u3"]["действие"]
    assert by_uuid["u3"]["персонажи"] == "c02"
    # Кадр 4 — второй кадр sc02: описание и персонажи из одной строки.
    assert "Игнат опускает глаза" in by_uuid["u4"]["действие"]
    assert by_uuid["u4"]["персонажи"] == "c01"


def test_extra_shots_do_not_shift_the_next_scene() -> None:
    """Лишний шот съедается своей сценой и не сдвигает соседнюю."""
    shots = [
        _shot("sc01", 1, "c01", "первый"),
        _shot("sc01", 2, "c01", "второй"),
        _shot("sc01", 3, "c01", "третий лишний"),
        _shot("sc02", 1, "c02", "чужая сцена"),
    ]
    chrono = [
        {"id_scene": "sc01", "кадры": [{"uuid": "u1"}]},
        {"id_scene": "sc02", "кадры": [{"uuid": "u2"}]},
    ]
    payload = build_local_assembler_payload(
        {"characters": [], "shot_plan_chrono": shots, "scenes_chrono": chrono},
        [_Frame("u1", 1), _Frame("u2", 2)],
    )
    by_uuid = {op["frame_uuid"]: op["fields"] for op in payload["ops"]}
    assert "первый" in by_uuid["u1"]["действие"]
    assert "чужая сцена" in by_uuid["u2"]["действие"]
