"""Каст рисуется и судится по одному тексту.

Живой прогон 2026-08-31: шаг «Персонажи» рисовал по hero_descriptions, а
vision судил по entities — все пять листов получили fail не за качество, а за
несовпадение. После выравнивания те же файлы дали pass без перерисовок.
"""

from __future__ import annotations

import pytest

from app.services.cast_source import cast_descriptions, describe_character_row


class _Project:
    id = 1
    hero_descriptions: list[str] = []


@pytest.fixture
def patched_rows(monkeypatch: pytest.MonkeyPatch):
    def _set(rows):
        async def fake(_session, _project):
            return rows

        monkeypatch.setattr("app.services.vision_check_db.load_character_rows", fake)

    return _set


def test_row_description_joins_look_clothes_rules() -> None:
    row = {"look": "синий шар", "clothes": "жёлтая сумка", "rules": "МЕДИУМ: 2D"}
    assert describe_character_row(row) == "синий шар жёлтая сумка МЕДИУМ: 2D"


def test_row_description_skips_empty_parts() -> None:
    assert describe_character_row({"look": "шар", "clothes": "", "rules": None}) == "шар"


@pytest.mark.asyncio
async def test_registry_wins_when_present(patched_rows) -> None:
    patched_rows(
        [
            {"id": "c01", "look": "человек", "clothes": "ветровка", "rules": ""},
            {"id": "c02", "look": "синий шар", "clothes": "сумка", "rules": ""},
        ]
    )
    p = _Project()
    p.hero_descriptions = ["совсем другой текст"]
    out, source = await cast_descriptions(None, p)
    assert source == "registry"
    assert out == ["человек ветровка", "синий шар сумка"]


@pytest.mark.asyncio
async def test_order_follows_codes_not_insertion(patched_rows) -> None:
    """c01 обязан быть первым: шаг нумерует пары по позиции, фото — по коду."""
    patched_rows(
        [
            {"id": "c03", "look": "третий", "clothes": "", "rules": ""},
            {"id": "c01", "look": "первый", "clothes": "", "rules": ""},
            {"id": "c02", "look": "второй", "clothes": "", "rules": ""},
        ]
    )
    out, _ = await cast_descriptions(None, _Project())
    assert out == ["первый", "второй", "третий"]


@pytest.mark.asyncio
async def test_falls_back_to_project_when_registry_empty(patched_rows) -> None:
    patched_rows([])
    p = _Project()
    p.hero_descriptions = ["ручной каст"]
    out, source = await cast_descriptions(None, p)
    assert (out, source) == (["ручной каст"], "project")


@pytest.mark.asyncio
async def test_rows_without_text_do_not_count(patched_rows) -> None:
    """Пустая карточка не должна вытеснять ручной каст."""
    patched_rows([{"id": "c01", "look": "", "clothes": "", "rules": ""}])
    p = _Project()
    p.hero_descriptions = ["ручной"]
    out, source = await cast_descriptions(None, p)
    assert (out, source) == (["ручной"], "project")


@pytest.mark.asyncio
async def test_registry_failure_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    async def boom(_session, _project):
        raise RuntimeError("база недоступна")

    monkeypatch.setattr("app.services.vision_check_db.load_character_rows", boom)
    p = _Project()
    p.hero_descriptions = ["запасной"]
    out, source = await cast_descriptions(None, p)
    assert (out, source) == (["запасной"], "project")
