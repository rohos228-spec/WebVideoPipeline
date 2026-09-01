"""Один cNN — одно тело: инвариант, на котором стоит раздача фотореференсов.

Живой прогон 2026-08-31: сцен-агент положил в c02 «трое взрослых круглых
существ». Кадр получил одну фотографию и расстановку на троих — генератор
нарисовал бы трёх клонов. Кодом это не ловилось нигде.
"""

from __future__ import annotations

import pytest

from app.services.cast_invariants import (
    check_cast_cards,
    codes_in_text,
    dangling_codes,
    multi_body_problem,
)


@pytest.mark.parametrize(
    "text",
    [
        "трое взрослых круглых существ ростом человеку по колено",
        "двое жителей в одинаковых фартуках",
        "группа круглых у прилавка",
        "три существа с хохолками",
    ],
)
def test_plural_description_is_flagged(text: str) -> None:
    assert multi_body_problem("c02", text) is not None


@pytest.mark.parametrize(
    "text",
    [
        "круглый шар без шеи, ярко-синий, уши-лопухи, глаза-точки",
        "мужчина около тридцати, поджарый, оливковая ветровка",
        "",
    ],
)
def test_single_body_description_passes(text: str) -> None:
    assert multi_body_problem("c01", text) is None


def test_crowd_mention_is_not_a_plural_body() -> None:
    """«Среди прохожих» — законное описание ОДНОГО тела в толпе."""
    assert multi_body_problem("c01", "стоит среди прохожих на рынке") is None


def test_codes_extracted_in_order_without_dupes() -> None:
    line = "влогер слева; c02 синий, c03 жёлтый и c02 снова в правой половине"
    assert codes_in_text(line) == ["c02", "c03"]


def test_dangling_codes_found() -> None:
    line = "Расстановка: c01 слева, c07 справа"
    assert dangling_codes(line, {"c01", "c02"}) == ["c07"]


def test_no_dangling_when_all_known() -> None:
    assert dangling_codes("c01 и c02 в кадре", {"c01", "c02", "c03"}) == []


def test_check_cards_flags_duplicate_code() -> None:
    cards = [
        {"code": "c01", "attrs": {"look": "человек"}},
        {"code": "c01", "attrs": {"look": "другой человек"}},
    ]
    problems = check_cast_cards(cards)
    assert any("больше одного раза" in p for p in problems)


def test_check_cards_flags_missing_code() -> None:
    assert any("без кода" in p for p in check_cast_cards([{"attrs": {"look": "шар"}}]))


def test_check_cards_clean_registry() -> None:
    cards = [
        {"code": "c01", "attrs": {"look": "мужчина около тридцати", "clothes": "ветровка"}},
        {"code": "c02", "attrs": {"look": "синий шар", "clothes": "жёлтая сумка"}},
    ]
    assert check_cast_cards(cards) == []


def test_check_cards_reads_russian_attr_keys() -> None:
    cards = [{"code": "c02", "attrs": {"внешность": "трое круглых существ"}}]
    assert check_cast_cards(cards) != []


# ── персонажей в кадре не больше, чем слотов референса ───────────────────


def test_within_slots_is_fine() -> None:
    from app.services.cast_invariants import too_many_characters

    assert too_many_characters(["c01", "c02"], 2) is None
    assert too_many_characters(["c01"], 2) is None
    assert too_many_characters([], 2) is None


def test_over_slots_names_the_codes_and_the_limit() -> None:
    from app.services.cast_invariants import too_many_characters

    problem = too_many_characters(["c01", "c02", "c03"], 2)
    assert problem is not None
    assert "c03" in problem and "2" in problem


def test_single_slot_provider_allows_only_one() -> None:
    """На MiniMax слот один — двое в кадре уже брак."""
    from app.services.cast_invariants import too_many_characters

    assert too_many_characters(["c01", "c02"], 1) is not None
    assert too_many_characters(["c01"], 1) is None


def test_zero_slots_disables_the_rule() -> None:
    """Провайдер без референсов — правило неприменимо, а не «всё брак»."""
    from app.services.cast_invariants import too_many_characters

    assert too_many_characters(["c01", "c02", "c03"], 0) is None


def test_frames_over_slots_reports_only_offenders_in_order() -> None:
    from app.services.cast_invariants import frames_over_ref_slots

    frames = {5: ["c01", "c02", "c03"], 2: ["c01"], 9: ["c01", "c02", "c03", "c04"]}
    assert [n for n, _ in frames_over_ref_slots(frames, 2)] == [5, 9]


def test_limit_matches_provider_catalog() -> None:
    """Правило и каталог — одна величина, а не два мнения."""
    from app.generation_options import ref_slots_for_provider
    from app.services.cast_invariants import too_many_characters

    slots = ref_slots_for_provider("outsee")
    assert too_many_characters(["c01"] * slots, slots) is None
    assert too_many_characters(["c01"] * (slots + 1), slots) is not None
