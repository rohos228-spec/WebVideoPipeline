"""Дефолт генератора медиа слушает .env, а не прибитый outsee-каталог.

`DEFAULTS` в generation_options указывают на модели Outsee. Когда на ноде
модель не выбрана (обычный случай: граф из шаблона, пикер не открывали),
шаг брал их и падал на «пустой ключ Outsee» — при явно выставленном в
окружении провайдере minimax.
"""

from __future__ import annotations

import pytest

from app.services.vibecode_catalog import (
    effective_image_generator_id,
    effective_video_generator_id,
)


class _Project:
    def __init__(self, image: str | None = None, video: str | None = None) -> None:
        self.meta: dict = {}
        self.image_generator = image
        self.video_generator = video


@pytest.fixture
def minimax_env(monkeypatch: pytest.MonkeyPatch):
    from app.settings import settings

    monkeypatch.setattr(settings, "image_provider", "minimax")
    monkeypatch.setattr(settings, "video_provider", "minimax")


def test_env_provider_picks_its_own_default(minimax_env) -> None:
    assert effective_image_generator_id(_Project()) == "minimax_image_01"
    assert effective_video_generator_id(_Project()) == "hailuo_2_3_fast"


def test_explicit_project_choice_still_wins(minimax_env) -> None:
    """Выбор человека сильнее .env — иначе настройка проекта ничего не значит."""
    assert effective_image_generator_id(_Project(image="nano_banana_2")) == "nano_banana_2"


def test_unknown_provider_falls_back_to_catalog_default(monkeypatch) -> None:
    from app.settings import settings

    monkeypatch.setattr(settings, "image_provider", "outsee")
    assert effective_image_generator_id(_Project()) == "gpt_image_2_vip"
