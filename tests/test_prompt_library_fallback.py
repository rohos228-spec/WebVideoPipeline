"""Каталог шага без `default.md`: resolve отдаёт первый доступный вариант.

Раньше оба выхода `resolve_project_prompt_with_source` возвращали
`("default", "default")` вне зависимости от того, лежит ли `default.md` на
диске — шаг падал на `FileNotFoundError` уже внутри `read_prompt`, хотя
рядом был рабочий вариант. `read_prompt` при этом намеренно остаётся
строгим: тихая подмена файла под тем же именем прячет причину.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import app.services.prompt_active_global as pag
import app.services.prompt_library as pl
from app.services.prompt_library import (
    DEFAULT_NAME,
    PROMPT_SOURCE_LABELS,
    resolve_project_prompt_with_source,
)


def test_fallback_source_has_label() -> None:
    assert PROMPT_SOURCE_LABELS["fallback"]


def test_resolve_falls_back_to_first_available(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    hero_dir = tmp_path / "04_hero"
    hero_dir.mkdir(parents=True)
    (hero_dir / "custom_model_sheet.md").write_text("Custom prompt content here...", encoding="utf-8")
    monkeypatch.setattr(pl, "PROMPTS_ROOT", tmp_path)
    monkeypatch.setattr(pag, "get_global_active", lambda _step: "")

    name, source = resolve_project_prompt_with_source({}, "hero")
    assert (name, source) == ("custom_model_sheet", "fallback")


def test_resolve_prefers_existing_default(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    hero_dir = tmp_path / "04_hero"
    hero_dir.mkdir(parents=True)
    (hero_dir / "custom_model_sheet.md").write_text("custom", encoding="utf-8")
    (hero_dir / f"{DEFAULT_NAME}.md").write_text("default", encoding="utf-8")
    monkeypatch.setattr(pl, "PROMPTS_ROOT", tmp_path)
    monkeypatch.setattr(pag, "get_global_active", lambda _step: "")

    assert resolve_project_prompt_with_source({}, "hero") == (DEFAULT_NAME, "default")


def test_resolve_empty_dir_keeps_default(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    (tmp_path / "04_hero").mkdir(parents=True)
    monkeypatch.setattr(pl, "PROMPTS_ROOT", tmp_path)
    monkeypatch.setattr(pag, "get_global_active", lambda _step: "")

    assert resolve_project_prompt_with_source({}, "hero") == (DEFAULT_NAME, "default")
