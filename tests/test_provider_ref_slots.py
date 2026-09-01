"""Слоты референса — свойство провайдера, а не константа кода.

`frame_cast.py` и мастер-промт картинок построены на «ровно одной фотографии
на кадр». Это правда для MiniMax image-01, выведенного из контура; Outsee
берёт два, и на живом прогоне 2026-08-31 в кадр уходило refs=2. По старому
правилу оценивали возможности пайплайна — и занижали: ограничение начинается
с третьего персонажа, а не со второго.
"""

from __future__ import annotations

import pytest

from app.generation_options import (
    DEFAULT_REF_SLOTS,
    PROVIDER_REF_SLOTS,
    ref_slots_for_provider,
)


def test_outsee_takes_two() -> None:
    assert ref_slots_for_provider("outsee") == 2


def test_minimax_takes_one() -> None:
    """Тот провайдер, под который писалось правило «фотография одна»."""
    assert ref_slots_for_provider("minimax") == 1


@pytest.mark.parametrize("raw", ["OUTSEE", " Outsee ", "outsee"])
def test_provider_name_is_normalised(raw: str) -> None:
    assert ref_slots_for_provider(raw) == 2


@pytest.mark.parametrize("raw", ["неизвестный", "", None])
def test_unknown_provider_is_conservative(raw: str | None) -> None:
    """Лишний реф провайдер отвергает запросом целиком — недобор безопаснее."""
    assert ref_slots_for_provider(raw) == DEFAULT_REF_SLOTS == 1


def test_catalog_values_are_sane() -> None:
    assert PROVIDER_REF_SLOTS and all(v >= 1 for v in PROVIDER_REF_SLOTS.values())


def test_generate_images_reads_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    """Шаг картинок берёт число из каталога, а не из своей константы."""
    from app.orchestrator.steps import generate_images as gi
    from app.settings import settings

    monkeypatch.setattr(settings, "image_provider", "minimax", raising=False)
    assert gi._max_refs() == 1
    monkeypatch.setattr(settings, "image_provider", "outsee", raising=False)
    assert gi._max_refs() == 2
