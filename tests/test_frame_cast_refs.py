"""Кому приедет фотография, а кого генератор увидит только со слов.

`image-01` у MiniMax принимает одну character-ссылку. Пайплайн грузил два
рефа на двухсоставный кадр, отправлял первый и молчал: на живом прогоне #2
кадр 12 — «женщина в сером пальто входит в вагон» — вышел мужчиной с лицом
c01.

Три варианта проверены на живых кадрах 12/13/17: с рефом и описанием одного
только второго героя второй всё равно берёт лицо первого; совсем без рефа
верным выходит второй, зато первый уезжает за единственным описанием (кадр
13 — оба героя стали женщинами). Работает третий: реф оставить, описать
обоих. Отсюда и правило ниже.

Правило выбора обязано совпадать с тем, как реф берёт шаг «Картинки», —
разойдутся, и промт опишет не того.
"""

from __future__ import annotations

from types import SimpleNamespace

from app.orchestrator.steps.generate_images import _parse_ref_ids
from app.services.db_frames_context import build_img_pr_db_context
from app.services.frame_cast import (
    cast_ids_in_order,
    characters_needing_description,
    reference_character,
)


def test_photo_goes_to_the_first_in_cast_order() -> None:
    assert reference_character("c01, c02") == "c01"
    assert reference_character("c02 женщина в сером пальто, c01 Игнат") == "c02"


def test_alone_in_frame_needs_no_words() -> None:
    """Фотография справляется сама; бюджет MiniMax тратить не на что."""
    assert characters_needing_description("c01") == []


def test_two_in_frame_means_describe_both() -> None:
    """Владельца фотографии тоже: без своей строки он растворяется в соседе."""
    assert characters_needing_description("c01, c02") == ["c01", "c02"]
    assert characters_needing_description("c02 женщина, c01 Игнат") == ["c02", "c01"]


def test_no_cast_means_no_reference_and_nobody_to_describe() -> None:
    for raw in ("", None, "нет", "—"):
        assert reference_character(raw) == ""
        assert characters_needing_description(raw) == []


def test_rule_matches_how_the_images_step_picks_the_ref() -> None:
    """Один порядок на два шага — иначе промт описывает не того героя."""
    for raw in (
        "c01, c02",
        "c02 женщина, c01 Игнат",
        "c01; c02",
        "c01 c02",
        "c02:, c01",
        "c01, c01, c02",
    ):
        from_images = [t for t in _parse_ref_ids(raw) if t.startswith("c")]
        # Шаг «Картинки» дублей не снимает — сравниваем по первому и составу.
        assert cast_ids_in_order(raw)[0] == from_images[0]
        assert set(cast_ids_in_order(raw)) == set(from_images)


def _frame(number: int, uuid: str, characters: str) -> SimpleNamespace:
    return SimpleNamespace(
        number=number,
        uuid=uuid,
        voiceover_text="закадр",
        meaning="",
        animation_prompt="",
        attrs={"characters": characters, "place": "вагон"},
    )


def test_context_tells_the_agent_whom_to_describe() -> None:
    ctx = build_img_pr_db_context(
        project_id=2,
        slug="s",
        frames=[
            _frame(12, "a" * 24, "c01, c02"),
            _frame(9, "b" * 24, "c01"),
            _frame(24, "c" * 24, ""),
        ],
        characters=[],
        include_field_map=True,
    )
    rows = {r["number"]: r for r in ctx["frames"]}

    # Двое в кадре: фото первому, а описываем обоих.
    assert rows[12]["ref_character"] == "c01"
    assert rows[12]["describe_appearance"] == "c01, c02"
    # Один в кадре: описывать некого, бюджет не тратим.
    assert rows[9]["ref_character"] == "c01"
    assert "describe_appearance" not in rows[9]
    # Людей нет вовсе — оба поля молчат.
    assert "ref_character" not in rows[24]
    assert "describe_appearance" not in rows[24]

    assert "ref_character" in ctx["field_map"]
    assert "describe_appearance" in ctx["field_map"]


def test_contract_points_all_carry_the_rule() -> None:
    """Правило должно стоять во всех трёх местах, иначе агент его не увидит."""
    from pathlib import Path

    from app.services.img_pr_batches import batch_footer
    from app.services.xlsx_step_runners import _IMG_PR_DB_HINT

    master = Path("prompts/05_image_prompts/default.md").read_text(encoding="utf-8")
    footer = batch_footer(batch_i=1, batch_n=1, n=8)
    for text in (master, footer, _IMG_PR_DB_HINT):
        assert "describe_appearance" in text
        assert "ref_character" in text


def test_missing_appearance_is_reported_not_swallowed() -> None:
    """Модель уронила внешность одного из двоих — это должно быть видно.

    Досочинить за неё нельзя: паспорт персонажа длиннее, чем остаток лимита.
    Но кадр 18 живого прогона ушёл вообще без строки «Референс», и в логе
    стоял ровный «успех».
    """
    from app.services.img_pr_continuity import _missing_appearances

    both = "Референс: c01 — мужчина 35 лет…; c02 — женщина 48 лет…\nФон: вагон."
    assert _missing_appearances(both, ["c01", "c02"]) == []
    only_one = "Референс: c01 — мужчина 35 лет…\nФон: вагон."
    assert _missing_appearances(only_one, ["c01", "c02"]) == ["c02"]
    none_at_all = "Место: последний вагон поезда, ночь.\nФон: вагон."
    assert _missing_appearances(none_at_all, ["c01", "c02"]) == ["c01", "c02"]
