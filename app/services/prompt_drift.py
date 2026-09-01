"""Промт узла изменился с прошлого прогона — сказать об этом вслух.

**Зачем.** В графе лежит *имя* варианта (`sd_action_vlog`), а текст живёт в
`prompts/`, который вне git и на проде примонтирован read-only. Один и тот же
JSON графа на двух машинах — два разных ролика; `NODE_SYSTEM.md` §1 обещает
«workflow = JSON, воспроизводим 1:1», и обещание нарушено. На живом прогоне
2026-08-31 это видно буквально: чтобы поменять поведение узла, файл
копировался по scp на сервер, а граф при этом не изменился ни на байт.

**Что делает этот модуль.** Первый шаг решения C из `ORCHESTRATOR-V2.md`
§3.8 (владелец выбрал «сначала хэш, потом база»): при каждом старте шага
запоминает отпечаток фактически использованного промта и, если он разошёлся
с прошлым прогоном того же узла, говорит об этом — в лог и в
``project.meta.prompt_fingerprints``.

**Чего он НЕ делает.** Не воспроизводит старый прогон: для этого текст
должен переехать в `prompt_versions`, и это следующий шаг. Здесь только
честность — «результат мог измениться не потому, что модель другая».

Хэш берётся из `input_hash.prompt_version_hash`, а не свой: тот же отпечаток
уже пишется в `llm_calls.prompt_version_hash`, и разводить два определения
одной величины — ровно та болезнь (две правды), от которой лечится всё
остальное.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from loguru import logger

META_KEY = "prompt_fingerprints"


def _fingerprints(project: Any) -> dict[str, Any]:
    meta = getattr(project, "meta", None)
    if not isinstance(meta, dict):
        return {}
    raw = meta.get(META_KEY)
    return dict(raw) if isinstance(raw, dict) else {}


def prompt_fingerprint(text: str) -> str:
    """Отпечаток текста промта. Один источник с учётом LLM-вызовов."""
    from app.services.input_hash import prompt_version_hash

    return prompt_version_hash(text or "")


def note_prompt_used(
    project: Any,
    *,
    node_key: str,
    step_code: str,
    variant: str,
    text: str,
) -> str | None:
    """Запомнить отпечаток промта узла. Вернуть прежний, если он был другим.

    ``None`` — узел запускается впервые либо промт не менялся. Возвращаемое
    значение существует для вызывающего, который может показать расхождение
    человеку; сам факт уже записан в лог и в ``project.meta``.
    """
    key = str(node_key or step_code or "").strip()
    if not key or not text:
        return None
    meta = getattr(project, "meta", None)
    if not isinstance(meta, dict):
        return None

    digest = prompt_fingerprint(text)
    store = _fingerprints(project)
    prev = store.get(key) if isinstance(store.get(key), dict) else None
    prev_hash = str((prev or {}).get("hash") or "") or None

    changed = bool(prev_hash) and prev_hash != digest
    if changed:
        logger.warning(
            "[#{}] промт узла {} изменился с прошлого прогона "
            "(вариант {!r}, было {}, стало {}) — результат мог поехать не из-за модели",
            getattr(project, "id", "?"),
            key,
            variant,
            prev_hash,
            digest,
        )

    store[key] = {
        "step": step_code,
        "variant": variant,
        "hash": digest,
        "at": datetime.now(UTC).isoformat(timespec="seconds"),
        **({"prev_hash": prev_hash} if changed else {}),
    }
    new_meta = dict(meta)
    new_meta[META_KEY] = store
    project.meta = new_meta
    return prev_hash if changed else None


def drifted_nodes(project: Any) -> list[dict[str, Any]]:
    """Узлы, чей промт разошёлся с прошлым прогоном. Для отчётов и UI."""
    out: list[dict[str, Any]] = []
    for key, rec in _fingerprints(project).items():
        if isinstance(rec, dict) and rec.get("prev_hash"):
            out.append({"node": key, **rec})
    return out
