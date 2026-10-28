"""Глобально активные варианты промтов (prompts/active_variants.json).

Последний сохранённый/активированный .md для шага — общий для всех проектов,
пока у проекта нет своего prompt_overrides[step_code].
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from loguru import logger

from app.services.prompt_library import PROMPTS_ROOT, prompt_path
from app.settings import settings

_ACTIVE_FILE = "active_variants.json"


def _active_write_path() -> Path:
    p = Path(settings.data_dir) / _ACTIVE_FILE
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _active_read_paths() -> list[Path]:
    return [
        Path(settings.data_dir) / _ACTIVE_FILE,
        PROMPTS_ROOT / _ACTIVE_FILE,
    ]


def load_global_active() -> dict[str, str]:
    for path in _active_read_paths():
        if not path.is_file():
            continue
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("prompt_active_global: bad {}: {}", path, e)
            continue
        if not isinstance(raw, dict):
            continue
        out: dict[str, str] = {}
        for k, v in raw.items():
            if isinstance(k, str) and isinstance(v, str) and v.strip():
                out[k] = v.strip()
        if out:
            return out
    return {}


def _save_global_active(data: dict[str, str]) -> None:
    path = _active_write_path()
    try:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except OSError as e:
        logger.warning("prompt_active_global: failed writing to {}: {}", path, e)
        try:
            (PROMPTS_ROOT / _ACTIVE_FILE).write_text(
                json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
        except OSError:
            pass


def get_global_active(step_code: str) -> str | None:
    name = load_global_active().get(step_code)
    if not name:
        return None
    try:
        if prompt_path(step_code, name).is_file():
            return name
    except ValueError:
        return None
    return None


def set_global_active(step_code: str, name: str) -> None:
    from app.services.prompt_library import ENRICH_STEP_CODES, EXCEL_GPT_UNIFIED_STEP

    if step_code in ENRICH_STEP_CODES:
        step_code = EXCEL_GPT_UNIFIED_STEP
    clean = (name or "").strip()
    if not clean:
        return
    try:
        if not prompt_path(step_code, clean).is_file():
            return
    except ValueError:
        return
    data = load_global_active()
    data[step_code] = clean
    _save_global_active(data)
    logger.debug("prompt_active_global: {} → {!r}", step_code, clean)


def sync_global_active_from_overrides(overrides: dict[str, Any] | None) -> None:
    if not overrides:
        return
    from app.services.prompt_library import ENRICH_STEP_CODES, EXCEL_GPT_UNIFIED_STEP, STEP_FOLDERS

    for step_code, name in overrides.items():
        if not isinstance(name, str):
            continue
        if step_code in ENRICH_STEP_CODES:
            set_global_active(EXCEL_GPT_UNIFIED_STEP, name.strip())
            continue
        if step_code in STEP_FOLDERS:
            set_global_active(step_code, name.strip())
