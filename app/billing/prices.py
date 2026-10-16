"""Цены операций в кредитах: берём цены каталога main (как в UI), $0.01 = 1 кредит."""

from __future__ import annotations

import math

from app.settings_billing import USD_PER_CREDIT

# Запасные цены, если модели нет в каталоге.
DEFAULT_CREDITS = {"video": 10, "image": 5, "llm": 1}
_ALIASES = {"video-basic": "video", "image-basic": "image", "llm-basic": "llm"}


def _usd_to_credits(usd: float) -> int:
    return max(1, math.ceil(round(usd / USD_PER_CREDIT, 6)))


def operation_price(model: str | None, kind: str | None = None, units: int = 1) -> int:
    """Стоимость операции в кредитах (целое, ≥1) за `units` штук."""
    units = max(1, int(units or 1))
    alias = _ALIASES.get((model or "").lower())
    if alias:
        return DEFAULT_CREDITS[alias] * units
    pricing: dict = {}
    try:
        from app.services.vibecode_catalog import find_model

        m = find_model(model) if model else None
        pricing = (m or {}).get("pricing") or {}
    except Exception:  # noqa: BLE001 — каталог недоступен: берём запасную цену
        pricing = {}
    usd = pricing.get("usd_per_video") if kind in (None, "video") else None
    if not usd and kind in (None, "image"):
        usd = pricing.get("usd_per_image")
    if usd:
        return _usd_to_credits(float(usd)) * units
    return DEFAULT_CREDITS.get(kind or "video", DEFAULT_CREDITS["video"]) * units
