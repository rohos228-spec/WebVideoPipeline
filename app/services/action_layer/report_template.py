"""Шаблон отчётов «по формату»: одно место, где описан вид таблиц и текстов.

Заказчик ещё не прислал свои образцы, поэтому здесь нейтральный формат по умолчанию:
таблица + короткий текст сверху. Чтобы поменять вид отчёта, правьте только этот файл:
порядок и подписи колонок, названия листов xlsx, заголовки, подписи полей кадра.
Ключи колонок (левая часть пар) — это имена полей в строках, их собирает код.
"""
from __future__ import annotations

# ── Отчёт по проекту: раздел «Работа с GPT» ──
GPT_SECTION_TITLE = "Работа с GPT"
GPT_NODE_COLUMNS: tuple[tuple[str, str], ...] = (
    ("label", "Нода"),
    ("prompt_title", "Промт (вариант)"),
    ("model", "Модель"),
    ("status_ru", "Статус"),
    ("frames_written", "Кадров заполнено"),
    ("fields_ru", "Какие поля"),
    ("characters_count", "Персонажей"),
    ("scenes_count", "Сцен"),
    ("note", "Примечание"),
)
# По строке на поле кадра, значение целиком: заказчику важно не терять данные в ячейках.
GPT_FRAME_COLUMNS: tuple[tuple[str, str], ...] = (
    ("label", "Нода"),
    ("frame", "Кадр"),
    ("field_ru", "Поле"),
    ("value", "Что записала"),
    ("overwritten", "Перезаписано"),
)
GPT_CHAT_COLUMNS: tuple[tuple[str, str], ...] = (
    ("label", "Нода"),
    ("prompt_title", "Промт"),
    ("status_ru", "Статус"),
    ("frames_written", "Кадров"),
    ("characters_count", "Персонажей"),
    ("scenes_count", "Сцен"),
)
GPT_SHEET = "Работа с GPT"
GPT_FRAMES_SHEET = "GPT по кадрам"
GPT_CHARACTERS_SHEET = "Персонажи и сцены"
GPT_LIST_COLUMNS: tuple[tuple[str, str], ...] = (
    ("label", "Нода"),
    ("kind", "Что"),
    ("name", "Название"),
)

# Подписи полей кадра (канонические имена из db_apply.FIELD_ALIASES). Нет в списке — пишем как есть.
FIELD_LABELS: dict[str, str] = {
    "voiceover_text": "закадр",
    "image_prompt": "промт картинки",
    "animation_prompt": "промт видео",
    "image_prompt_shot2": "промт картинки 2",
    "animation_prompt_shot2": "промт видео 2",
    "meaning": "смысл",
    "duration_seconds": "длительность",
    "characters": "персонажи",
    "main_action": "главное действие",
    "place": "место",
    "accent": "акцент",
    "scene_sense": "смысл сцены",
    "visual_type": "тип сцены",
    "scene_feature": "особенность сцены",
    "cluster": "кластер",
    "scene_structure": "структура сцены",
    "edit_type": "тип стыка",
    "scene_transition": "переход",
    "scene_start_words": "начало сцены",
    "scene_end_words": "конец сцены",
    "general_plan": "общий план",
    "script_text": "закадровый текст",
}

# ── Отчёт по нескольким проектам ──
PROJECTS_TITLE = "Отчёт по проектам"
PROJECTS_COLUMNS: tuple[tuple[str, str], ...] = (
    ("project_id", "№"),
    ("title", "Проект"),
    ("status_ru", "Статус"),
    ("steps", "Шагов готово"),
    ("gpt", "Работа с GPT (промты)"),
    ("frames", "Кадров"),
    ("images", "Картинок"),
    ("videos", "Видео"),
    ("spent_usd", "Траты, $ (оценка)"),
    ("updated_at", "Обновлён"),
)
PROJECTS_CHAT_COLUMNS: tuple[tuple[str, str], ...] = (
    ("project_id", "№"),
    ("title", "Проект"),
    ("status_ru", "Статус"),
    ("steps", "Шаги"),
    ("frames", "Кадров"),
    ("images", "Картинок"),
    ("videos", "Видео"),
    ("spent_usd", "$"),
)
PROJECTS_SHEET = "Проекты"
SUMMARY_SHEET = "Итог"
MAX_PROJECTS = 20

# Сколько строк показывать в чате (полная версия — в файле).
CHAT_ROWS = 10
COLUMN_WIDTH_MAX = 60


def field_label(key: str) -> str:
    return FIELD_LABELS.get(key, key)
