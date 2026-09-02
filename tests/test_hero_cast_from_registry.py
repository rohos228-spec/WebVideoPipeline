"""Число героев диктует реестр, а не колонка hero_count.

Живой прогон 2026-08-31: vision судит листы по реестру `entities`, а рисовал
шаг по `hero_descriptions` + `hero_count` — пять листов забракованы не за
качество, а за несовпадение с параллельной правдой. Тест держит развилку в
`generate_hero.run`: источник — реестр → и описания, и их ЧИСЛО из него же.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.models import ProjectStatus
from app.orchestrator.steps import generate_hero as gh


def _spy_pairs(seen: dict):
    """Подсмотреть n_total, с которым шаг пришёл к расчёту целей."""

    def _pairs(n_total: int, variations_cfg: list[int]) -> list[tuple[int, int]]:
        seen["n"] = n_total
        return [(i, 1) for i in range(1, n_total + 1)]

    return _pairs


def _project(**kw) -> SimpleNamespace:
    base = dict(
        id=61,
        status=ProjectStatus.generating_hero,
        meta={},
        hero_mode="auto",
        hero_count=1,
        hero_variations=[],
        hero_description=None,
        hero_descriptions=["из колонки"],
    )
    base.update(kw)
    return SimpleNamespace(**base)


@pytest.mark.asyncio
async def test_registry_count_overrides_hero_count_column() -> None:
    """Реестр на двоих при hero_count=1 — целей две, а не одна."""
    p = _project()
    seen: dict = {}

    async def _pairs(session, project):
        return {(i, v) for i in range(1, 9) for v in range(1, 6)}

    with (
        patch.object(gh, "_load_excel_hero_from_xlsx", new=AsyncMock(return_value=None)),
        patch(
            "app.services.cast_source.cast_descriptions",
            new=AsyncMock(return_value=(["жёлтый лесник", "рыжая белка"], "registry")),
        ),
        patch.object(gh, "_approved_pairs", side_effect=_pairs),
        patch.object(gh, "_hero_target_pairs", side_effect=_spy_pairs(seen)),
    ):
        await gh.run(AsyncMock(), p, AsyncMock())

    assert seen["n"] == 2, "число героев обязано прийти из реестра"
    assert p.status is ProjectStatus.hero_ready


@pytest.mark.asyncio
async def test_project_source_keeps_hero_count_column() -> None:
    """Реестра нет — hero_count остаётся законом, развилка не срабатывает."""
    p = _project()
    seen: dict = {}

    with (
        patch.object(gh, "_load_excel_hero_from_xlsx", new=AsyncMock(return_value=None)),
        patch(
            "app.services.cast_source.cast_descriptions",
            new=AsyncMock(return_value=(["из колонки"], "project")),
        ),
        patch.object(
            gh,
            "_approved_pairs",
            new=AsyncMock(return_value={(i, v) for i in range(1, 9) for v in range(1, 6)}),
        ),
        patch.object(gh, "_hero_target_pairs", side_effect=_spy_pairs(seen)),
    ):
        await gh.run(AsyncMock(), p, AsyncMock())

    assert seen["n"] == 1
    assert p.status is ProjectStatus.hero_ready
