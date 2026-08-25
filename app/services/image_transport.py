"""Нужен ли Chrome для генерации картинок.

Браузерный путь (Outsee через CDP) — наследие машины владельца. На сервере
Chrome нет и не будет (см. Dockerfile), там работают только HTTP-провайдеры:
grsai, outsee по API и MiniMax.

Проверка «есть ли HTTP-провайдер» жила в `generate_hero` и знала про grsai и
outsee, но не про MiniMax — его добавили позже. Поэтому при
`IMAGE_PROVIDER=minimax` шаг героев всё равно требовал Chrome и падал на
`Connect call failed ('127.0.0.1', 29229)`. Найдено живым прогоном 2026-08-26.
"""

from __future__ import annotations


def http_image_primary() -> bool:
    """True — картинки идут по HTTP, браузерная сессия не нужна."""
    from app.bots.grsai import grsai_enabled
    from app.bots.minimax import minimax_enabled
    from app.bots.outsee_http import outsee_api_configured, outsee_api_enabled_for_image

    return bool(
        minimax_enabled() or grsai_enabled() or outsee_api_enabled_for_image() or outsee_api_configured()
    )
