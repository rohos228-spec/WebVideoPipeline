"""Недобор фаз action чинится битами скелета, а не роняет шаг.

Скелет режет ячейку по смысловым сдвигам («1 бит = 1 слот = 1 кадр»), action
считает фазы по времени («фаз ≈ время_сек / 3»). На плотной короткой ячейке
законы дают разные числа. Раньше это был жёсткий стоп с повтором того же
промпта — то есть гарантированный тупик.
"""

from __future__ import annotations

import pytest

from app.services.scene_design.agents import SceneDesignAgentError
from app.services.scene_design.skeleton import (
    repair_action_phases_from_bits,
    validate_action_covers_skeleton_bits,
)


def _skeleton() -> dict:
    return {
        "scenes": [
            {
                "id_scene": "scene_05",
                "биты": [
                    {"глагол": "входит", "изменение": "перрон → вагон", "якорь": "Входит женщина"},
                    {
                        "глагол": "садится",
                        "изменение": "стоит → сидит напротив",
                        "якорь": "Садится напротив",
                    },
                ],
            }
        ]
    }


def _action_with(n_phases: int) -> list[dict]:
    return [
        {
            "id_scene": "scene_05",
            "цепь_действия": [
                {"phase_index": i + 1, "beat": "setup", "action": f"фаза {i + 1}"} for i in range(n_phases)
            ],
        }
    ]


def test_missing_phase_is_taken_from_the_bit() -> None:
    scenes = _action_with(1)
    repairs = repair_action_phases_from_bits(scenes, _skeleton())
    assert repairs == ["scene_05: +1 фаз из битов скелета"]

    chain = scenes[0]["цепь_действия"]
    assert len(chain) == 2
    added = chain[-1]
    assert added["из_бита_скелета"] is True
    assert added["phase_index"] == 2
    # Текст фазы — глагол бита и правая часть «было → стало».
    assert added["action"] == "садится — сидит напротив"
    # Сцена без payoff получает его на последней фазе: арка не остаётся открытой.
    assert added["beat"] == "payoff"

    # После починки гейт молчит — это и было целью.
    validate_action_covers_skeleton_bits(scenes, _skeleton())


def test_enough_phases_are_left_alone() -> None:
    scenes = _action_with(3)
    assert repair_action_phases_from_bits(scenes, _skeleton()) == []
    assert len(scenes[0]["цепь_действия"]) == 3


def test_existing_payoff_is_not_moved() -> None:
    scenes = _action_with(1)
    scenes[0]["цепь_действия"][0]["beat"] = "payoff"
    repair_action_phases_from_bits(scenes, _skeleton())
    beats = [ph["beat"] for ph in scenes[0]["цепь_действия"]]
    assert beats == ["payoff", "develop"]


def test_gate_still_fails_when_there_is_nothing_to_repair_from() -> None:
    """Бит без глагола и изменения фазой не станет — тут стоп законен."""
    skeleton = {"scenes": [{"id_scene": "scene_05", "биты": [{}, {}]}]}
    scenes = _action_with(1)
    assert repair_action_phases_from_bits(scenes, skeleton) == []
    with pytest.raises(SceneDesignAgentError, match="биты скелета без фазы"):
        validate_action_covers_skeleton_bits(scenes, skeleton)


def test_repair_matches_scene_by_position_when_ids_differ() -> None:
    """id_scene разъехались — связываем по порядку, как это делает сам гейт."""
    scenes = [{"id_scene": "иначе_названа", "цепь_действия": [{"phase_index": 1, "action": "одна"}]}]
    assert repair_action_phases_from_bits(scenes, _skeleton())
    assert len(scenes[0]["цепь_действия"]) == 2
