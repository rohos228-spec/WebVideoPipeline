"""Персистентный курсор прогресса шага с привязкой к входу (этап 2).

Единый формат для всех чекпоинтов (файловый img_pr, chunk'и scene_design,
``ai_jobs`` в project.meta, батчи apply_ops, NodeRun.meta):

    {"v": 1, "input_hash": "ih1:…", "done_keys": [...], "updated_at": "…"}

Правила (спека cache-resume):

- курсор ВАЛИДЕН только при совпадении ``input_hash`` с текущим входом;
  mismatch или legacy-чекпоинт без hash → курсор сбрасывается (протухший
  результат не переиспользуется, лог «invalidated: input changed»);
- в ``done_keys`` попадают только ПОЛНЫЕ принятые единицы: salvage
  (`_salvaged_partial`) и батчи, применённые до падения соседнего, —
  не cache hit, они доезжают добором/повтором;
- хранение — там, где чекпоинт уже жил (файл/meta): модуль формирует и
  валидирует, но не навязывает столбец.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from loguru import logger

from app.services.input_hash import hashes_match

CURSOR_VERSION = 1


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_cursor(input_hash: str) -> dict[str, Any]:
    return {
        "v": CURSOR_VERSION,
        "input_hash": input_hash,
        "done_keys": [],
        "updated_at": _now_iso(),
    }


def load_cursor(
    raw: Any,
    current_hash: str,
    *,
    ctx: str = "",
) -> dict[str, Any]:
    """Курсор из хранилища → валидный курсор для текущего входа.

    Возвращает исходный курсор, только если он корректной формы И его
    input_hash совпал с текущим; иначе — свежий пустой (инвалидация).
    """
    if (
        isinstance(raw, dict)
        and isinstance(raw.get("done_keys"), list)
        and hashes_match(raw.get("input_hash"), current_hash)
    ):
        return {
            "v": CURSOR_VERSION,
            "input_hash": current_hash,
            "done_keys": [str(k) for k in raw["done_keys"]],
            "updated_at": str(raw.get("updated_at") or _now_iso()),
        }
    if isinstance(raw, dict) and raw.get("done_keys"):
        logger.info(
            "step_progress{}: чекпоинт invalidated: input changed "
            "(was={}, now={}, дропнуто done={})",
            f"[{ctx}]" if ctx else "",
            str(raw.get("input_hash"))[:24],
            current_hash[:24],
            len(raw.get("done_keys") or []),
        )
    return new_cursor(current_hash)


def is_done(cursor: dict[str, Any], key: str) -> bool:
    return str(key) in set(cursor.get("done_keys") or [])


def done_set(cursor: dict[str, Any]) -> set[str]:
    return {str(k) for k in cursor.get("done_keys") or []}


def mark_done(cursor: dict[str, Any], *keys: str) -> dict[str, Any]:
    """Пометить единицы завершёнными (только полные принятые результаты).

    Возвращает НОВЫЙ dict — JSON-колонки SQLAlchemy не видят мутацию
    того же объекта (см. save_ai_job_checkpoint).
    """
    done = list(dict.fromkeys([*(cursor.get("done_keys") or []), *map(str, keys)]))
    return {
        "v": CURSOR_VERSION,
        "input_hash": cursor["input_hash"],
        "done_keys": done,
        "updated_at": _now_iso(),
    }
