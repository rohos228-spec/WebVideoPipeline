"""Непрерывность: ось действия и реестр предметов считаются кодом.

Смысл проверок — не «функция вернула словарь», а поведение, которого от
модели добиться промтом не удалось: сцена держит одну сторону оси, предмет
не телепортируется и не воскресает.
"""

from __future__ import annotations

from app.services.scene_design.continuity import (
    SIDE_A,
    SIDE_B,
    SIDE_NEUTRAL,
    Axis,
    PropState,
    build_prop_ledger,
    continuity_line,
    enforce_axis,
    normalize_side,
    parse_character_ids,
    resolve_axis,
    screen_directions,
)


def test_parse_character_ids_keeps_order_and_pads() -> None:
    assert parse_character_ids("c8 врач, c02 мать, c8 снова") == ["c08", "c02"]
    assert parse_character_ids(["c1", "c12"]) == ["c01", "c12"]
    assert parse_character_ids(None) == []


def test_normalize_side_understands_both_alphabets() -> None:
    assert normalize_side("A") == SIDE_A
    assert normalize_side("а") == SIDE_A
    assert normalize_side("Б") == SIDE_B
    assert normalize_side("нейтраль") == SIDE_NEUTRAL
    assert normalize_side("") is None
    assert normalize_side("как-нибудь") is None


def test_axis_left_is_the_one_who_appeared_first() -> None:
    shots = [
        {"кто_в_кадре": "c01 Игнат"},
        {"кто_в_кадре": "c01 Игнат, c02 получатель"},
        {"кто_в_кадре": "c02 получатель"},
    ]
    assert resolve_axis(shots) == Axis(left="c01", right="c02")


def test_axis_needs_two_people() -> None:
    assert resolve_axis([{"кто_в_кадре": "c01"}, {"кто_в_кадре": "c01"}]) is None


def test_silent_shots_inherit_the_scene_side() -> None:
    """Модель промолчала — сцена всё равно снята с одной стороны."""
    shots, violations = enforce_axis([{"uuid": "u1"}, {"uuid": "u2"}, {"uuid": "u3"}])
    assert [s["сторона"] for s in shots] == [SIDE_A, SIDE_A, SIDE_A]
    assert violations == []


def test_axis_jump_without_neutral_is_repaired() -> None:
    shots, violations = enforce_axis(
        [
            {"uuid": "u1", "сторона": "A"},
            {"uuid": "u2", "сторона": "B"},
            {"uuid": "u3"},
        ]
    )
    assert [s["сторона"] for s in shots] == [SIDE_A, SIDE_A, SIDE_A]
    assert [v.kind for v in violations] == ["axis_jump"]
    assert violations[0].frame_uuid == "u2"


def test_axis_crossing_through_neutral_is_allowed() -> None:
    shots, violations = enforce_axis(
        [
            {"uuid": "u1", "сторона": "A"},
            {"uuid": "u2", "сторона": "нейтраль"},
            {"uuid": "u3", "сторона": "B"},
            {"uuid": "u4"},
        ]
    )
    assert [s["сторона"] for s in shots] == [SIDE_A, SIDE_NEUTRAL, SIDE_B, SIDE_B]
    assert violations == []


def test_screen_directions_mirror_on_the_far_side() -> None:
    axis = Axis(left="c01", right="c02")
    a = screen_directions(axis, SIDE_A)
    b = screen_directions(axis, SIDE_B)
    assert "в левой половине" in a["c01"] and "смотрит вправо" in a["c01"]
    assert "в правой половине" in a["c02"] and "смотрит влево" in a["c02"]
    assert a["c01"] != b["c01"]
    assert "в правой половине" in b["c01"]


def test_prop_holder_carries_forward_without_being_repeated() -> None:
    frames = [
        {
            "uuid": "u1",
            "персонажи": "c01",
            "действие": "достаёт конверт из сумки",
            "предметы": [{"id": "p01", "как": "вводится", "у_кого": "c01"}],
        },
        {"uuid": "u2", "персонажи": "c01", "действие": "теребит край бумаги"},
        {"uuid": "u3", "персонажи": "c01", "действие": "смотрит в окно"},
    ]
    ledger, violations = build_prop_ledger(frames)
    assert violations == []
    assert ledger["u3"]["p01"].holder == "c01"


def test_prop_teleport_is_caught_and_reverted() -> None:
    frames = [
        {
            "uuid": "u1",
            "персонажи": "c01",
            "действие": "достаёт конверт",
            "предметы": [{"id": "p01", "как": "вводится", "у_кого": "c01"}],
        },
        {
            "uuid": "u2",
            "персонажи": "c01, c02",
            "действие": "оба молча смотрят друг на друга",
            "предметы": [{"id": "p01", "как": "держится", "у_кого": "c02"}],
        },
    ]
    ledger, violations = build_prop_ledger(frames)
    assert [v.kind for v in violations] == ["prop_teleport"]
    assert ledger["u2"]["p01"].holder == "c01"


def test_prop_changes_hands_when_the_action_shows_the_transfer() -> None:
    frames = [
        {
            "uuid": "u1",
            "персонажи": "c01",
            "действие": "достаёт конверт",
            "предметы": [{"id": "p01", "как": "вводится", "у_кого": "c01"}],
        },
        {
            "uuid": "u2",
            "персонажи": "c01, c02",
            "действие": "протягивает конверт через проход",
            "предметы": [{"id": "p01", "как": "меняется", "у_кого": "c02"}],
        },
        {"uuid": "u3", "персонажи": "c02", "действие": "убирает во внутренний карман"},
    ]
    ledger, violations = build_prop_ledger(frames)
    assert violations == []
    assert ledger["u3"]["p01"].holder == "c02"


def test_prop_does_not_come_back_after_it_left() -> None:
    frames = [
        {
            "uuid": "u1",
            "персонажи": "c01, c02",
            "действие": "отдаёт конверт",
            "предметы": [{"id": "p01", "как": "вводится", "у_кого": "c01"}],
        },
        {
            "uuid": "u2",
            "персонажи": "c02",
            "действие": "уносит конверт за двери",
            "предметы": [{"id": "p01", "как": "уходит"}],
        },
        {
            "uuid": "u3",
            "персонажи": "c01",
            "действие": "сидит один",
            "предметы": [{"id": "p01", "как": "держится", "у_кого": "c01"}],
        },
    ]
    ledger, violations = build_prop_ledger(frames)
    assert [v.kind for v in violations] == ["prop_after_exit"]
    assert ledger["u3"]["p01"].gone is True


def test_holder_must_be_in_the_frame() -> None:
    frames = [
        {
            "uuid": "u1",
            "персонажи": "c02",
            "действие": "садится напротив",
            "предметы": [{"id": "p01", "как": "вводится", "у_кого": "c01"}],
        }
    ]
    _ledger, violations = build_prop_ledger(frames)
    assert [v.kind for v in violations] == ["prop_absent_holder"]


def test_continuity_line_states_facts_not_rules() -> None:
    line = continuity_line(
        axis=Axis(left="c01", right="c02"),
        side=SIDE_A,
        names={"c01": "Игнат", "c02": "женщина в сером пальто"},
        props={"p01": PropState(prop_id="p01", verb="держится", holder="c01")},
        prop_names={"p01": "жёлтый конверт"},
        in_frame=["c01", "c02"],
    )
    assert "Игнат в левой половине кадра, смотрит вправо" in line
    assert "женщина в сером пальто в правой половине кадра, смотрит влево" in line
    assert "жёлтый конверт держит Игнат" in line
    assert "180" not in line


def test_continuity_line_skips_people_outside_the_frame() -> None:
    line = continuity_line(
        axis=Axis(left="c01", right="c02"),
        side=SIDE_A,
        names={"c01": "Игнат", "c02": "женщина"},
        in_frame=["c01"],
    )
    assert "Игнат" in line
    assert "женщина" not in line


def test_continuity_line_is_empty_when_there_is_nothing_to_hold() -> None:
    assert continuity_line(axis=None, side=SIDE_A) == ""


def test_prop_is_not_named_when_its_holder_left_the_frame() -> None:
    """Женщина вышла с конвертом — в кадре с одним Игнатом конверта нет."""
    line = continuity_line(
        axis=None,
        side=SIDE_A,
        names={"c01": "Игнат", "c02": "женщина"},
        props={"p01": PropState(prop_id="p01", verb="держится", holder="c02")},
        prop_names={"p01": "конверт"},
        in_frame=["c01"],
    )
    assert line == ""


def test_scene_axis_follows_the_film_not_the_scene_entrance() -> None:
    """Сцена, где первым показали второго героя, не переворачивает расстановку."""
    from app.services.scene_design.continuity import axis_for_scene

    film = Axis(left="c01", right="c02")
    # В этой сцене c02 попал в кадр раньше — локально ось встала бы наоборот.
    scene = [{"кто_в_кадре": "c02"}, {"кто_в_кадре": "c02, c01"}, {"кто_в_кадре": "c01"}]
    assert resolve_axis(scene) == Axis(left="c02", right="c01")
    assert axis_for_scene(scene, film) == film


def test_scene_with_another_pair_gets_its_own_axis() -> None:
    from app.services.scene_design.continuity import axis_for_scene

    film = Axis(left="c01", right="c02")
    scene = [{"кто_в_кадре": "c03"}, {"кто_в_кадре": "c03, c04"}]
    assert axis_for_scene(scene, film) == Axis(left="c03", right="c04")
