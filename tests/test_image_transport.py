"""При HTTP-провайдере картинок шаги не требуют Chrome.

На сервере Chrome нет (Dockerfile), и `IMAGE_PROVIDER=minimax`. Проверка «есть
ли HTTP-провайдер» знала про grsai и outsee, но не про MiniMax — его добавили
позже. Шаг героев открывал браузерную сессию безусловно и падал на
`Connect call failed ('127.0.0.1', 29229)`. Найдено живым прогоном 2026-08-26.
"""

from __future__ import annotations

import pytest

from app.services.image_transport import http_image_primary


def _minimax(monkeypatch, on: bool) -> None:
    from app.settings import settings

    monkeypatch.setattr(settings, "image_provider", "minimax" if on else "outsee")
    monkeypatch.setattr(settings, "minimax_api_key", "sk-проба" if on else "")
    # Остальные провайдеры выключены, чтобы проверять именно MiniMax.
    monkeypatch.setattr(settings, "grsai_api_key", "", raising=False)
    monkeypatch.setattr(settings, "outsee_api_key", "", raising=False)


def test_minimax_counts_as_http(monkeypatch) -> None:
    _minimax(monkeypatch, True)
    assert http_image_primary() is True


def test_no_provider_means_browser(monkeypatch) -> None:
    _minimax(monkeypatch, False)
    monkeypatch.setattr("app.bots.grsai.grsai_enabled", lambda: False)
    monkeypatch.setattr("app.bots.outsee_http.outsee_api_enabled_for_image", lambda: False)
    monkeypatch.setattr("app.bots.outsee_http.outsee_api_configured", lambda: False)
    assert http_image_primary() is False


@pytest.mark.asyncio
async def test_hero_step_skips_browser_when_http(monkeypatch) -> None:
    """Сессия не открывается — даже попытка соединиться с CDP запрещена."""
    from app.orchestrator.steps import generate_hero

    _minimax(monkeypatch, True)

    def _boom():
        raise AssertionError("шаг полез в Chrome при HTTP-провайдере")

    monkeypatch.setattr(generate_hero, "browser_session", _boom)
    async with generate_hero._optional_browser_session(
        need_cdp=not generate_hero._excel_hero_http_primary()
    ) as bs:
        assert bs is None


@pytest.mark.asyncio
async def test_items_step_skips_browser_when_http(monkeypatch) -> None:
    from app.orchestrator.steps import generate_items

    _minimax(monkeypatch, True)

    def _boom():
        raise AssertionError("шаг полез в Chrome при HTTP-провайдере")

    monkeypatch.setattr(generate_items, "browser_session", _boom)
    async with generate_items._optional_browser(need_cdp=not http_image_primary()) as bs:
        assert bs is None
