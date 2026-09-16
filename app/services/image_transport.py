"""Нужен ли Chrome для генерации картинок и видео.

Браузерный путь (Outsee через CDP) — наследие машины владельца. На сервере
Chrome нет и не будет (см. Dockerfile), там работают только HTTP-провайдеры:
outsee по API и MiniMax.

Проверка «есть ли HTTP-провайдер» жила в `generate_hero` и знала про outsee, но не про MiniMax — его добавили позже. Поэтому при
`IMAGE_PROVIDER=minimax` шаг героев всё равно требовал Chrome и падал на
`Connect call failed ('127.0.0.1', 29229)`. Найдено живым прогоном 2026-08-26.
"""

from __future__ import annotations


def http_image_primary() -> bool:
    """True — картинки идут по HTTP, браузерная сессия не нужна."""
    from app.bots.minimax import minimax_enabled
    from app.bots.outsee_http import outsee_api_configured, outsee_api_enabled_for_image

    return bool(minimax_enabled() or outsee_api_enabled_for_image() or outsee_api_configured())


def http_video_primary() -> bool:
    """True — видео идёт по HTTP, браузерная сессия не нужна.

    Та же история, что с картинками, и найдена тем же способом — следующим
    шагом живого прогона: `generate_videos` держал свою копию проверки без
    MiniMax (Hailuo).
    """
    from app.bots.minimax import minimax_video_enabled
    from app.bots.outsee_http import outsee_api_configured, outsee_api_enabled_for_video

    return bool(minimax_video_enabled() or outsee_api_enabled_for_video() or outsee_api_configured())
