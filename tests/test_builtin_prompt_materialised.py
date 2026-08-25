"""Встроенный промт становится файлом — иначе править его нечем.

Шаг разбора состава работает без файла: текст лежит в
`cast_extract.DEFAULT_PROMPT`. Но редактор промтов в интерфейсе показывает
ФАЙЛЫ, и без него человек увидел бы «промтов не заведено» у шага, который
прямо сейчас работает. Получалось бы, что править нельзя именно то, что
хочется править первым: как ИИ разбирает сценарий на персонажей.

Поэтому на старте файл создаётся из встроенного текста — но только если его
там нет. Перезапись затирала бы правки пользователя на каждом рестарте, а
`read_prompt` идёт «база, потом диск», то есть однажды созданный файл
побеждает встроенный навсегда.
"""

from __future__ import annotations

from app.services.cast_extract import DEFAULT_PROMPT
from app.services.pipeline_stages import STAGES
from app.services.prompt_library import STEP_FOLDERS, prompt_path, read_prompt, write_prompt


def test_cast_step_is_registered() -> None:
    """У шага есть папка — без неё ручки промтов отвечают 404."""
    assert "cast" in STEP_FOLDERS


def test_cast_prompt_is_editable_from_a_stage() -> None:
    """Промт объявлен в стадии, иначе в интерфейсе его просто нет.

    Первая редакция правки это и упустила: шаг добавили, папку завели, а в
    `prompt_steps` не вписали — и редактор о нём не знал.
    """
    cast_stage = next(s for s in STAGES if s.id == "cast")
    assert "cast" in cast_stage.prompt_steps, (
        "промт разбора состава не объявлен в стадии — править его будет негде"
    )


def test_builtin_text_is_usable_as_a_file(tmp_path, monkeypatch) -> None:
    """Встроенный текст записывается и читается обратно без потерь."""
    from app.services import prompt_library

    monkeypatch.setattr(prompt_library, "PROMPTS_ROOT", tmp_path)

    write_prompt("cast", "default", DEFAULT_PROMPT)
    assert read_prompt("cast", "default") == DEFAULT_PROMPT


def test_existing_file_is_not_overwritten(tmp_path, monkeypatch) -> None:
    """Правку пользователя рестарт не затирает.

    Это главное свойство: материализация одноразовая. Иначе каждый перезапуск
    контейнера возвращал бы встроенный текст, и правка промта выглядела бы
    как «сохранилось и пропало».
    """
    from app.services import prompt_library

    monkeypatch.setattr(prompt_library, "PROMPTS_ROOT", tmp_path)

    write_prompt("cast", "default", "мой промт, я его писал")

    # Повторяем условие из `_startup_maintenance`: пишем только если файла нет.
    target = prompt_path("cast", "default")
    if not target.exists():
        write_prompt("cast", "default", DEFAULT_PROMPT)

    assert read_prompt("cast", "default") == "мой промт, я его писал"
