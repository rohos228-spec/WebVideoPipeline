"""prompt_is_hollow: «нет/отсутствует» про предмет сцены — содержание, про входные данные — заглушка.

Детектор теперь решает и запись img_pr, и платный гейт картинок, поэтому ложное
«пусто» у здоровой сцены блокирует работу, а ложное «есть сцена» у заглушки — платит.
"""

from __future__ import annotations

import pytest

from app.services.action_layer.img_pr_guard import prompt_is_hollow

STYLE = " STYLE: Archival Noir Watercolor, dark wash, halftone dots."

HEALTHY = [
    "Пустой ночной перрон, людей нет." + STYLE,
    "Фон: заброшенный цех, окон нет. Действие: людей нет. Свет: электричества нет." + STYLE,
    "Свет: источник не указан, мягкий рассеянный." + STYLE,
    "Фон: пустыня, тень отсутствует, жара." + STYLE,
    "Фон: нет исходных данных о фоне, но лиса бежит по снегу." + STYLE,
    "Фон: нет исходных данных о фоне но лиса бежит по снегу." + STYLE,
]
HOLLOW = [
    "Фон: нет исходных данных, но сохранить нейтральный фон. "
    "Действие: нет исходных данных, однако оставить кадр пустым." + STYLE,
    "Фон: нет исходных данных, но общий стиль сохраняется. "
    "Действие: нет исходных данных, зато стиль единый." + STYLE,
    "Фон: нет исходных данных но сохранить нейтральный фон." + STYLE,
    "Фон: данных о фоне нет. Действие: информация о действии отсутствует." + STYLE,
    "Фон: описание локации не указано. Свет: не задан." + STYLE,
    "Нет исходных данных для заполнения." + STYLE,
]


@pytest.mark.parametrize("text", HEALTHY)
def test_absent_scene_object_is_content(text: str) -> None:
    assert prompt_is_hollow(text) is False


@pytest.mark.parametrize("text", HOLLOW)
def test_missing_source_data_is_hollow(text: str) -> None:
    assert prompt_is_hollow(text) is True
