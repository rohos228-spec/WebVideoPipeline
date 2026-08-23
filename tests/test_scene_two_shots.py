"""Сцена с двумя людьми не снимается как галерея портретов.

Заказчик называет нерешёнными два барьера, и первый — смена планов при
сохранении позиции актёров. Читать её неоткуда, если обоих ни разу не показали
вместе. Живой прогон #2: разговор двоих в вагоне — 26 шотов из 32 про одного,
4 двухсоставных из 16 в сценах, где оба присутствуют.

Две точки отказа проверяются отдельно: камера, которая так снимает, и сборщик,
который сужал состав кадра до одного субъекта уже после камеры.
"""

from __future__ import annotations

import pytest

from app.services.scene_design.agents import (
    SceneDesignAgentError,
    validate_chrono_dyn_camera_two_shots,
)
from app.services.scene_design.assembler import _cast_for_shot


def _shot(scene: str, who: str) -> dict:
    return {"id_scene": scene, "кто_в_кадре": who, "крупность": "средний план"}


def _plan(pairs_per_scene: int, solo_per_scene: int, scenes: int = 4) -> list[dict]:
    out: list[dict] = []
    for i in range(scenes):
        sid = f"scene_{i:02d}"
        out += [_shot(sid, "c01 Игнат, c02 женщина") for _ in range(pairs_per_scene)]
        out += [_shot(sid, "c01 Игнат") for _ in range(solo_per_scene)]
    return out


def test_dialogue_shot_as_two_monologues_is_rejected() -> None:
    with pytest.raises(SceneDesignAgentError) as err:
        validate_chrono_dyn_camera_two_shots(_plan(pairs_per_scene=1, solo_per_scene=4))
    text = str(err.value)
    assert "галерея портретов" in text
    assert "scene_00" in text  # худшие сцены названы — это фидбек на ретрай


def test_ladder_of_shot_sizes_passes() -> None:
    validate_chrono_dyn_camera_two_shots(_plan(pairs_per_scene=2, solo_per_scene=3))


def test_scene_about_one_person_is_not_a_defect() -> None:
    """Герой один в кадре по сюжету — правило к сцене не применяется."""
    validate_chrono_dyn_camera_two_shots([_shot("scene_01", "c01 Игнат") for _ in range(20)])


def test_short_plan_is_not_judged_by_share() -> None:
    """На четырёх шотах доля ничего не значит — гейт молчит."""
    validate_chrono_dyn_camera_two_shots(
        [_shot("scene_01", "c01, c02"), *[_shot("scene_01", "c01") for _ in range(3)]]
    )


def test_live_run_camera_would_have_been_rejected() -> None:
    """Ровно та раскадровка, что дала портретную галерею на прогоне #2."""
    shots = [
        *[_shot("scene_07", "c01 Игнат") for _ in range(2)],
        _shot("scene_07", "c02 женщина"),
        *[_shot("scene_10", "c01 Игнат"), _shot("scene_10", "c02 женщина")],
        *[_shot("scene_12", "c02 женщина") for _ in range(2)],
        *[_shot("scene_12", "c01 Игнат") for _ in range(2)],
        _shot("scene_05", "c01 Игнат, c02 женщина"),
        _shot("scene_05", "c01 Игнат, c02 женщина"),
    ]
    with pytest.raises(SceneDesignAgentError):
        validate_chrono_dyn_camera_two_shots(shots)


def test_camera_keeps_the_second_person_when_it_agrees_who_acts() -> None:
    """Субъект фазы больше не затирает двухсоставный шот камеры."""
    assert _cast_for_shot("c01", "", "c01 Игнат, c02 женщина") == "c01, c02"


def test_phase_wins_when_camera_disagrees_who_acts() -> None:
    """Разошлись — берём фазу: иначе «руки женщины» достаются c01."""
    assert _cast_for_shot("c01", "", "c02 женщина") == "c01"


def test_in_frame_of_the_phase_is_used_when_camera_is_silent() -> None:
    assert _cast_for_shot("c01", "c01, c02", "") == "c01, c02"


def test_close_up_of_one_stays_a_close_up_of_one() -> None:
    """Камера вправе взять крупный план одного из тех, кто в кадре фазы."""
    assert _cast_for_shot("c01", "c01, c02", "c01 Игнат") == "c01"


def test_stamping_the_phase_no_longer_narrows_the_shot_to_one_person() -> None:
    """Сквозная проверка: камера сняла двоих — в кадре остаются двое.

    ``stamp_actions_onto_ops`` подставляет действие и субъект той фазы, что
    досталась кадру. Субъект при этом затирал `персонажи` целиком, и
    двухсоставный шот камеры превращался в кадр про одного — расстановка
    непрерывности теряла второго ещё до промта картинки.
    """
    from app.services.scene_design.assembler import stamp_actions_onto_ops

    chrono = [
        {
            "id_scene": "sc01",
            "кадры": [{"uuid": "u1"}, {"uuid": "u2"}],
            "цепь_действия": [
                {
                    "phase_index": 1,
                    "action": "Игнат подаётся вперёд",
                    "subject": "c01",
                    "в_кадре": "c01, c02",
                },
                {
                    "phase_index": 2,
                    "action": "женщина забирает конверт",
                    "subject": "c02",
                    "в_кадре": "c02, c01",
                },
            ],
        }
    ]
    payload = {
        "ops": [
            # Камера сняла обоих и согласна, кто действует.
            {"frame_uuid": "u1", "fields": {"персонажи": "c01 Игнат, c02 женщина"}},
            # Камера взяла крупный план одного — это её право.
            {"frame_uuid": "u2", "fields": {"персонажи": "c02 женщина"}},
        ]
    }
    out = stamp_actions_onto_ops(payload, {"scenes_chrono": chrono})
    by_uuid = {op["frame_uuid"]: op["fields"] for op in out["ops"]}
    assert by_uuid["u1"]["персонажи"] == "c01, c02"
    assert by_uuid["u1"]["действие"] == "Игнат подаётся вперёд"
    assert by_uuid["u2"]["персонажи"] == "c02"


def test_gate_runs_on_the_merged_plan_not_only_per_chunk() -> None:
    """Долю можно считать только на всей раскадровке.

    Реальный прогон чанкуется (3 куска по ≤10 кадров), и ветка склейки —
    единственное место, где виден весь shot_plan. Проверка, стоящая только
    в ``parse_agent_slice``, на живом прогоне не выполняется вообще: камера
    отдала 6/24 двухсоставных и прошла.
    """
    import inspect

    from app.services.scene_design import agent_chunks

    src = inspect.getsource(agent_chunks.merge_agent_slices)
    assert "accept_slice" in src


def test_scene_without_payoff_is_repaired_not_rejected() -> None:
    """Три попытки живого прогона ушли на гейт, который лечится одной строкой.

    Последняя фаза сцены по построению и есть её видимый итог — надо назвать
    бит, а не просить модель переписать цепь заново.
    """
    from app.services.scene_design.agents import (
        repair_missing_payoff,
        validate_chrono_dyn_action_scenes,
    )

    scenes = [
        {
            "id_scene": f"scene_{i:02d}",
            "location": "loc01",
            "связь_с_прошлой": "нить",
            "крючок_в_следующую": "дальше",
            "цепь_действия": [
                {
                    "phase_index": 1,
                    "beat": "setup",
                    "action": "Игнат толкает конверт к её руке",
                    "subject": "c01",
                    "переход_к_следующей": "cut",
                },
                {
                    "phase_index": 2,
                    "beat": "develop",
                    "action": "женщина сжимает конверт в перчатке",
                    "subject": "c02",
                    "переход_к_следующей": "cut",
                },
                {
                    "phase_index": 3,
                    "beat": "develop",
                    "action": "Игнат вжимается лопатками в спинку",
                    "subject": "c01",
                    "переход_к_следующей": "cut",
                },
                {
                    "phase_index": 4,
                    "beat": "develop",
                    "action": "перчатка прячет конверт под полу пальто",
                    "subject": "c02",
                    "переход_к_следующей": "cut",
                },
            ],
        }
        for i in range(1, 7)
    ]
    fixed = repair_missing_payoff(scenes)
    assert len(fixed) == 6
    assert all(sc["цепь_действия"][-1]["beat"] == "payoff" for sc in scenes)
    # Тот же гейт, что ронял шаг три попытки подряд, теперь молчит.
    validate_chrono_dyn_action_scenes(scenes)


def test_existing_payoff_is_left_alone() -> None:
    from app.services.scene_design.agents import repair_missing_payoff

    scenes = [
        {
            "id_scene": "scene_01",
            "цепь_действия": [
                {"beat": "payoff", "action": "Игнат разжимает пальцы", "subject": "c01"},
                {"beat": "develop", "action": "конверт летит к полу", "subject": "c01"},
            ],
        }
    ]
    assert repair_missing_payoff(scenes) == []
    assert scenes[0]["цепь_действия"][-1]["beat"] == "develop"


def test_people_are_counted_even_when_named_without_ids() -> None:
    """Контракт камеры разрешает «имя/роль человека» вместо cNN.

    Живой прогон вернул ``кто_в_кадре: «Игнат»`` — счётчик, искавший только
    `cNN`, намерил ноль двухсоставных шотов, и проверка доли прошла на пустом
    знаменателе: зелёная и ничего не проверившая.
    """
    from app.services.scene_design.agents import _shot_cast

    assert _shot_cast({"кто_в_кадре": "c01 Игнат, c02 женщина"}) == ["c01", "c02"]
    assert len(_shot_cast({"кто_в_кадре": "Игнат, женщина в сером пальто"})) == 2
    assert len(_shot_cast({"кто_в_кадре": "Игнат"})) == 1
    for empty in ("", "нет", "—"):
        assert _shot_cast({"кто_в_кадре": empty}) == []


def test_gate_does_not_pass_silently_when_scene_ids_disagree() -> None:
    """Камера назвала сцены иначе — падаем обратно на её же состав, не на ноль."""
    shots = [{"id_scene": "scene_p1", "кто_в_кадре": "Игнат"} for _ in range(9)] + [
        {"id_scene": "scene_p1", "кто_в_кадре": "Игнат, женщина"}
    ]
    with pytest.raises(SceneDesignAgentError):
        validate_chrono_dyn_camera_two_shots(shots, expect_scenes={"scene_05", "scene_06"})


def test_camera_scene_ids_are_pulled_back_to_action() -> None:
    """Камера обязана называть сцены так же, как action.

    Живой прогон вернул `scene_p1` и `scene_21` при сценах `scene_01…09`.
    Сборщик после этого не связывает шот со сценой и падает на позиционное
    выравнивание — та самая ошибка, из-за которой в кадре оказывались
    описание одного шота и персонажи другого.
    """
    from app.services.scene_design.agents import repair_camera_scene_ids

    action = [{"id_scene": f"scene_{i:02d}"} for i in range(1, 4)]
    shots = [
        {"id_scene": "scene_p1", "кто_в_кадре": "c01"},
        {"id_scene": "scene_p1", "кто_в_кадре": "c01"},
        {"id_scene": "scene_21", "кто_в_кадре": "c01, c02"},
    ]
    assert repair_camera_scene_ids(shots, action) == ["scene_p1 → scene_01", "scene_21 → scene_02"]
    assert [sh["id_scene"] for sh in shots] == ["scene_01", "scene_01", "scene_02"]


def test_correct_scene_ids_are_left_alone() -> None:
    from app.services.scene_design.agents import repair_camera_scene_ids

    action = [{"id_scene": "scene_01"}, {"id_scene": "scene_02"}]
    shots = [{"id_scene": "scene_02"}, {"id_scene": "scene_01"}]
    assert repair_camera_scene_ids(shots, action) == []
    assert [sh["id_scene"] for sh in shots] == ["scene_02", "scene_01"]
