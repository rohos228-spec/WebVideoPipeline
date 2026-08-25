"""Стадия сообщает, какие промты к ней относятся.

Семь карточек интерфейса — это свёртка двух десятков статусов. За «Героями и
предметами» стоит пять папок промтов, за «Озвучкой и сборкой» — ни одной.
Состав объявлен на сервере (`Stage.prompt_steps`), потому что фронт его
восстановить не может: он видит только карточки.

Тест держит две вещи, которые молча ломаются:

* **несуществующий шаг.** Опечатка в `prompt_steps` не падает нигде — просто в
  интерфейсе не появится редактор, и это спишут на «так задумано»;
* **потерянная стадия.** Стадия без промтов законна (озвучка), но стадия,
  которая ЗОВЁТ модель и при этом ничего не объявила, — почти наверняка
  забытая строка.
"""

from __future__ import annotations

from app.services.pipeline_stages import STAGES
from app.services.prompt_library import STEP_FOLDERS


def test_declared_steps_exist_in_the_library() -> None:
    """Каждый объявленный шаг имеет папку промтов."""
    bad: list[str] = []
    for stage in STAGES:
        for code in stage.prompt_steps:
            if code not in STEP_FOLDERS:
                bad.append(f"{stage.id} → {code}")
    assert not bad, (
        f"стадии ссылаются на шаги без папки промтов: {bad} — редактор промта в интерфейсе просто не появится"
    )


def test_entry_step_is_covered_where_it_has_a_prompt() -> None:
    """Входной шаг стадии, если у него есть промт, обязан быть в списке.

    Именно его запускает кнопка «Сгенерировать», и именно его промт человек
    пойдёт править первым, когда результат не устроит.
    """
    missing: list[str] = []
    for stage in STAGES:
        entry = stage.entry_step
        if entry in STEP_FOLDERS and entry not in stage.prompt_steps:
            missing.append(f"{stage.id} (входной шаг {entry})")
    assert not missing, f"входной шаг не попал в список промтов стадии: {missing}"


def test_text_stages_declare_something() -> None:
    """Стадии, которые пишут текст моделью, не могут быть без промтов."""
    for stage_id in ("plan", "script", "frames"):
        stage = next(s for s in STAGES if s.id == stage_id)
        assert stage.prompt_steps, (
            f"стадия {stage_id} обращается к модели, но промтов не объявила — править будет нечего"
        )


def test_final_stage_is_honestly_empty() -> None:
    """У озвучки и сборки промтов нет, и это объявлено пустым списком.

    Пустой список — не забывчивость: сборка делается кодом и ffmpeg, модель там
    не участвует. Проверка закрепляет разницу между «нет промтов» и «промты
    забыли объявить».
    """
    final = next(s for s in STAGES if s.id == "final")
    assert final.prompt_steps == ()
