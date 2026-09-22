"""Каталог голосов ElevenLabs и чтение выбора из Project.meta."""

from __future__ import annotations

import time

from app.models import Project

DEFAULT_ELEVENLABS_VOICE_ID = "pNInz6obpgDQGcFmaJgB"  # Адам (Adam)

ELEVENLABS_VOICES: tuple[dict[str, str], ...] = (
    {
        "id": "pNInz6obpgDQGcFmaJgB",
        "name": "Адам (Adam)",
        "description": "глубокий, эпичный голос рассказчика (рекомендуется)",
    },
    {
        "id": "JBFqnCBsd6RMkjVDRZzb",
        "name": "Джордж (George)",
        "description": "тёплый, харизматичный сторителлер",
    },
    {
        "id": "nPczCjzI2devNBz1zQrb",
        "name": "Брайан (Brian)",
        "description": "бархатный, солидный диктор",
    },
    {
        "id": "IKne3meq5aSn9XLyUdCD",
        "name": "Чарли (Charlie)",
        "description": "уверенный, энергичный голос",
    },
    {
        "id": "TX3LPaxmHKxFdv7VOQHJ",
        "name": "Лиам (Liam)",
        "description": "молодой, современный голос",
    },
    {
        "id": "Xb7hH8MSUJpSbSDYk0k2",
        "name": "Алиса (Alice)",
        "description": "чёткий, выразительный женский голос",
    },
    {
        "id": "EXAVITQu4vr4xnSDxMaL",
        "name": "Сара (Sarah)",
        "description": "уверенный, зрелый женский голос",
    },
    {
        "id": "hpp4J3VqNfWAUOO0d1Us",
        "name": "Белла (Bella)",
        "description": "тёплый, яркий женский голос",
    },
    {
        "id": "pFZP5JQG7iQjIQuC4Bku",
        "name": "Лили (Lily)",
        "description": "бархатный, кинематографичный женский голос",
    },
)

_VALID_IDS = frozenset(v["id"] for v in ELEVENLABS_VOICES)


def resolve_elevenlabs_voice_id(project: Project) -> str:
    """ID голоса из meta.node_step_params.audio или Адам по умолчанию.

    Неизвестный id (опечатка/удалённый голос) — ValueError, а не тихий
    откат на Адама: иначе оператор не узнает, что выбранный голос не работает.
    """
    meta = getattr(project, "meta", None) or {}
    raw = meta.get("node_step_params")
    if not isinstance(raw, dict):
        return DEFAULT_ELEVENLABS_VOICE_ID
    audio = raw.get("audio")
    if not isinstance(audio, dict):
        return DEFAULT_ELEVENLABS_VOICE_ID
    vid = audio.get("elevenlabs_voice_id")
    if vid is None or (isinstance(vid, str) and not vid.strip()):
        return DEFAULT_ELEVENLABS_VOICE_ID
    if isinstance(vid, str) and vid in _VALID_IDS:
        return vid
    raise ValueError(f"elevenlabs_voice_id {vid!r} нет в каталоге — выбери голос заново")


_VOICES_PROBE_TTL_S = 3600.0
_voices_probe_cache: dict = {"at": 0.0, "live": None}


async def probe_live_voice_ids(*, max_age_s: float = _VOICES_PROBE_TTL_S) -> set[str] | None:
    """Живые id голосов из GET /v1/voices (бесплатно, символы не списывает).

    Возвращает None, если проверить не удалось (нет ключа/сети) — тогда
    звонящий решает сам, а не блокирует шаг.
    """
    import httpx

    now = time.monotonic()
    cached = _voices_probe_cache
    if cached["live"] is not None and now - cached["at"] < max_age_s:
        return set(cached["live"])
    from app import settings as settings_mod

    key = (getattr(settings_mod.settings, "elevenlabs_api_key", None) or "").strip()
    if not key:
        return None
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(
                "https://api.elevenlabs.io/v1/voices",
                headers={"xi-api-key": key},
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception:  # noqa: BLE001
        return None
    live = {str(v.get("voice_id") or "").strip() for v in (data.get("voices") or [])}
    live.discard("")
    cached["at"] = now
    cached["live"] = live
    return live
