"""Подписи слотов шага 5 — «Доработка данных», а не «Доп работа с EXCEL».

Excel из текстового контура убран (книга больше не идёт контекстом), и подписи
не должны обещать таблицу. Меняются только человекочитаемые тексты: коды шагов
и `callback_data` остаются `enrich_<i>` — на них завязаны хендлеры и meta.

Тест держит все четыре состояния слота сразу: идёт, сделан, можно запустить,
рано. Каждое рисует свою подпись, и раньше все четыре говорили про EXCEL.
"""

from __future__ import annotations

import pytest

from app.models import Project, ProjectStatus
from app.telegram.menu import enrich_submenu_kb


def _project(status: ProjectStatus) -> Project:
    p = Project(slug="t", topic="Вулканы", status=status)
    p.id = 3
    return p


def _labels(status: ProjectStatus) -> list[str]:
    kb = enrich_submenu_kb(_project(status))
    return [b.text for row in kb.inline_keyboard for b in row]


@pytest.mark.parametrize(
    ("status", "marker"),
    [
        (ProjectStatus.enriching_1, "⏳ Доработка данных #1 · идёт…"),
        (ProjectStatus.enrich_1_ready, "✅ Доработка данных #1 (перезапустить)"),
        (ProjectStatus.hero_ready, "▶ Доработка данных #1"),
        (ProjectStatus.planning, "⚙ Доработка данных #1 (настроить заранее)"),
    ],
)
def test_slot_label_per_state(status: ProjectStatus, marker: str) -> None:
    assert marker in _labels(status)


def test_no_excel_in_any_label() -> None:
    for status in (ProjectStatus.planning, ProjectStatus.hero_ready, ProjectStatus.enrich_1_ready):
        joined = " ".join(_labels(status))
        assert "EXCEL" not in joined.upper()


def test_callback_data_still_says_enrich() -> None:
    """Подписи переименованы, адреса — нет: на `enrich_<i>` завязаны хендлеры."""
    kb = enrich_submenu_kb(_project(ProjectStatus.hero_ready))
    cbs = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert "proj:3:step:enrich_1" in cbs
