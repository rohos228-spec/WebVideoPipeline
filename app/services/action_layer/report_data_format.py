"""Формат отчёта «данные работы с GPT»: какие колонки, в каком порядке и с какими подписями.

Пока заказчик не прислал свой образец, «формат» — это наборы колонок ниже. Свой формат
заказчика сюда кладётся так:

1. Нужной колонки нет в ``FRAME_COLUMNS`` — добавить ключ: подпись и канонические поля кадра,
   из которых брать значение (первое непустое). Канонические имена — как в
   ``app/services/db_apply.py::FIELD_ALIASES`` (``place``, ``characters``, ``shot01_action``…).
2. Добавить набор в ``PRESETS``: имя → колонки по порядку.
3. Сделать его набором по умолчанию (``DEFAULT_PRESET``) или назвать словами в ``PRESET_WORDS``,
   чтобы чат брал его по просьбе («как у заказчика»).

Код отчёта (``report_gpt_data.py``) колонок не знает: он собирает значения кадра по каноническим
полям, а этот модуль решает, что из них показать.
"""

from __future__ import annotations

import re

# Колонка таблицы кадров: ключ → (подпись, канонические поля кадра по порядку).
# Пустой кортеж полей — колонку заполняет сам отчёт (номер кадра, кто записал).
FRAME_COLUMNS: dict[str, tuple[str, tuple[str, ...]]] = {
    "frame": ("№ кадра", ()),
    "voiceover_text": ("Закадровый текст", ("voiceover_text",)),
    "scene": ("Сцена", ("shot01_id_scene", "scene_id")),
    "place": ("Место", ("place",)),
    "characters": ("Персонажи", ("characters",)),
    "action": ("Действие", ("shot01_action", "main_action")),
    "description": ("Описание кадра", ("shot01_description", "meaning")),
    "background": ("Фон", ("shot01_bg",)),
    "props": ("Предметы", ("shot01_props",)),
    "scene_sense": ("Смысл сцены", ("scene_sense",)),
    "lighting": ("Свет", ("lighting", "scene_lighting")),
    "image_prompt": ("Промт картинки", ("image_prompt",)),
    "image_prompt_shot2": ("Промт картинки 2", ("image_prompt_shot2",)),
    "animation_prompt": ("Промт видео", ("animation_prompt",)),
    "duration_seconds": ("Секунд", ("duration_seconds",)),
    "nodes": ("Кто записал", ()),
}

PRESETS: dict[str, tuple[str, ...]] = {
    # По умолчанию: что в кадре и кто в нём.
    "standard": ("frame", "voiceover_text", "scene", "place", "characters", "action"),
    # «Только сцены и персонажи».
    "scenes_characters": ("frame", "scene", "place", "characters"),
    # «С промтами картинок» / «промты картинок».
    "image_prompts": ("frame", "voiceover_text", "scene", "characters", "image_prompt"),
    "animation_prompts": ("frame", "voiceover_text", "scene", "characters", "animation_prompt"),
    "voiceover": ("frame", "voiceover_text"),
    # «Все поля», «полностью».
    "full": (
        "frame",
        "voiceover_text",
        "scene",
        "place",
        "characters",
        "action",
        "description",
        "background",
        "props",
        "scene_sense",
        "lighting",
        "image_prompt",
        "image_prompt_shot2",
        "animation_prompt",
        "duration_seconds",
        "nodes",
    ),
}
DEFAULT_PRESET = "standard"

# Слова просьбы → набор. Порядок важен: первое совпадение побеждает.
PRESET_WORDS: tuple[tuple[str, str], ...] = (
    (r"вс[её]\s+пол|все\s+колонк|полност|целиком|подробн", "full"),
    (r"только\s+(?:сцен\w*\s+и\s+персонаж|персонаж\w*\s+и\s+сцен)", "scenes_characters"),
    (r"только\s+закадр|только\s+текст", "voiceover"),
)
# «С промтами картинок» к любому набору добавляет колонку, а не меняет набор.
EXTRA_WORDS: tuple[tuple[str, str], ...] = (
    (r"пром(?:п)?т\w*\s+(?:для\s+)?(?:картин|изображ|img)", "image_prompt"),
    (r"пром(?:п)?т\w*\s+(?:для\s+)?(?:видео|анимац)", "animation_prompt"),
    (r"(?<![а-я])фон\w*", "background"),
    (r"(?<![а-я])предмет\w*", "props"),
    (r"(?<![а-я])свет\w*|освещен", "lighting"),
    (r"описани\w*\s+кадр", "description"),
    (r"кто\s+(?:что\s+)?записал|какая\s+нода", "nodes"),
)

# Таблица персонажей и сцен (листы xlsx и разделы html).
CHARACTER_COLUMNS: tuple[tuple[str, str], ...] = (
    ("id", "ID"),
    ("name", "Имя"),
    ("look", "Внешность"),
    ("clothes", "Одежда"),
    ("char", "Характер"),
    ("frames", "В кадрах"),
    ("source", "Откуда"),
)
SCENE_COLUMNS: tuple[tuple[str, str], ...] = (
    ("id", "Сцена"),
    ("place", "Место"),
    ("sense", "Смысл сцены"),
    ("characters", "Персонажи"),
    ("frames", "Кадры"),
    ("source", "Откуда"),
)
NODE_COLUMNS: tuple[tuple[str, str], ...] = (
    ("label", "Нода"),
    ("status_ru", "Статус"),
    ("frames_written", "Кадров записала"),
    ("fields_ru", "Какие поля"),
    ("characters", "Персонажей"),
    ("scenes", "Сцен"),
    ("note", "Примечание"),
)

TITLE = "Отчёт по данным работы с GPT"
FRAMES_SHEET = "Кадры"
CHARACTERS_SHEET = "Персонажи"
SCENES_SHEET = "Сцены"
NODES_SHEET = "Ноды GPT"
FILE_PREFIX = "gpt_data_report"
# Чат: сколько кадров в таблице и сколько знаков в ячейке (полное — в файле).
CHAT_ROWS = 10
CHAT_CELL = 90
CHAT_TABLE_CHARS = 3200
CHAT_CHARACTERS = 6
CHAT_SCENES = 6


def columns_for(preset: str | None = None, columns: list[str] | tuple[str, ...] | None = None) -> list[str]:
    """Колонки отчёта: явный список, иначе набор; «№ кадра» всегда первой."""
    keys = [c for c in (columns or ()) if c in FRAME_COLUMNS]
    if not keys:
        keys = list(PRESETS.get(preset or DEFAULT_PRESET) or PRESETS[DEFAULT_PRESET])
    keys = list(dict.fromkeys(keys))
    if "frame" in keys:
        keys.remove("frame")
    return ["frame", *keys]


def columns_from_text(text: str) -> tuple[str, list[str]]:
    """Набор и добавленные колонки по словам просьбы: («standard», ["image_prompt"])."""
    low = " ".join((text or "").lower().replace("ё", "е").split())
    preset = DEFAULT_PRESET
    for pattern, name in PRESET_WORDS:
        if re.search(pattern, low):
            preset = name
            break
    extra = [key for pattern, key in EXTRA_WORDS if re.search(pattern, low)]
    if preset == "full":
        extra = []
    return preset, [key for key in extra if key not in PRESETS[preset]]


def labelled(keys: list[str]) -> tuple[tuple[str, str], ...]:
    return tuple((key, FRAME_COLUMNS[key][0]) for key in keys if key in FRAME_COLUMNS)
