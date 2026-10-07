"""Каталог русских голосов ElevenLabs (200 проверенных голосов).

Поддерживает выбор голоса, поиск, фильтрацию по полу и доступ к сэмплам.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from app.project_root import find_project_root

_ROOT = find_project_root()
_CATALOG_PATH = Path(__file__).resolve().parent / "ru_voices_catalog.json"
_SAMPLES_DIR = _ROOT / "data" / "voices" / "samples"
_FALLBACK_FEMALE_DIR = Path(r"C:\Users\EternalFlow\Downloads\Telegram Desktop\ru_voices_female\ru_voices_female\mp3\female")
_FALLBACK_MALE_DIR = Path(r"C:\Users\EternalFlow\Downloads\Telegram Desktop\ru_voices_male\ru_voices_male\mp3\male")


@lru_cache(maxsize=1)
def get_ru_voices() -> list[dict]:
    """Возвращает полный список 200 русских голосов."""
    if _CATALOG_PATH.is_file():
        with open(_CATALOG_PATH, encoding="utf-8") as f:
            return json.load(f)
    return []


def get_ru_voice_by_id(voice_id: str) -> dict | None:
    """Поиск голоса по 20-значному ElevenLabs ID."""
    clean_id = (voice_id or "").strip()
    if not clean_id:
        return None
    for v in get_ru_voices():
        if v.get("id") == clean_id:
            return v
    return None


def get_sample_path(voice_id: str) -> Path | None:
    """Возвращает путь к MP3-файлу образца для указанного голоса."""
    clean_id = (voice_id or "").strip()
    if not clean_id:
        return None

    # Основной путь: скопированный сэмпл в data/voices/samples/
    primary = _SAMPLES_DIR / f"{clean_id}.mp3"
    if primary.is_file():
        return primary

    # Фолбэк 1: поиск по имени файла из каталога
    voice = get_ru_voice_by_id(clean_id)
    if voice and voice.get("filename"):
        fn = voice["filename"]
        gender = voice.get("gender")
        if gender == "female" and _FALLBACK_FEMALE_DIR.is_dir():
            fallback_f = _FALLBACK_FEMALE_DIR / fn
            if fallback_f.is_file():
                return fallback_f
        elif gender == "male" and _FALLBACK_MALE_DIR.is_dir():
            fallback_m = _FALLBACK_MALE_DIR / fn
            if fallback_m.is_file():
                return fallback_m

    return None
