"""Что модель должна знать при повторе шага: прошлая ошибка и замечания ревьюера.

Повтор шага раньше шёл тем же промтом, что и упавший запуск, — модель повторяла ту же
ошибку. Источники текста, по порядку:

- файл автоповтора оркестратора (``orchestrator_autonomy.write_feedback``) — разбор сбоя
  или находки проверки содержания; у «Работы с GPT» он читается в
  ``gpt_operator_client`` по ключу ноды, у остальных шагов — по коду шага;
- ошибка прошлого запуска из ``meta.step_failure``, если это повтор воркера (мягкий
  повтор или пробуждение после сна): ручной запуск человека снимает счётчик
  ``total_fails`` (``clear_failure_backoff_for_manual_start``) — и текста не будет;
- замечания авто-ревью ``meta.auto_fix_hints`` (``auto_advance._apply_regen``) — только
  для того шага, который ревьюер отправил переделывать.

Блоки дописываются в конец сообщения модели. Замечания ревьюера снимаются после
успешного шага (``clear_review_hints``), ошибка — сама при успехе
(``clear_failure_on_success``).
"""

from __future__ import annotations

from typing import Any

from app.contracts.policy import FEEDBACK_HEADER

REVIEW_HEADER = "# ЗАМЕЧАНИЯ РЕВЬЮЕРА (учти при переделке)"
HINTS_KEY = "auto_fix_hints"
HINTS_FOR_KEY = "auto_fix_hints_for"
_ERROR_CHARS = 1500
_HINTS_MAX = 12


def _meta(project: Any) -> dict:
    meta = getattr(project, "meta", None)
    return meta if isinstance(meta, dict) else {}


def step_feedback(project: Any, step_code: str, running: str | None) -> str:
    """Текст «почему прошлый запуск отклонён» для повтора шага; пусто — первый запуск."""
    from app.services.orchestrator_autonomy import FEEDBACK_CHARS, read_feedback

    parts: list[str] = []
    data_dir = getattr(project, "data_dir", None)
    if data_dir is not None:
        try:
            text = read_feedback(data_dir, step_code)
        except Exception:  # noqa: BLE001
            text = ""
        if text:
            parts.append(text)
    fs = _meta(project).get("step_failure")
    if running and isinstance(fs, dict) and str(fs.get("last_running") or "") == running:
        totals = fs.get("total_fails") if isinstance(fs.get("total_fails"), dict) else {}
        error = str(fs.get("last_error") or "").strip()
        if error and int(totals.get(running) or 0) >= 1 and error not in "\n".join(parts):
            parts.append(f"Прошлый запуск шага упал с ошибкой: {error[:_ERROR_CHARS]}")
    return "\n".join(parts)[:FEEDBACK_CHARS]


def review_hints(project: Any, running: str | None) -> list[str]:
    """Замечания авто-ревью для этого шага. Без отметки шага (старые записи) — тоже наши."""
    meta = _meta(project)
    raw = meta.get(HINTS_KEY)
    if not raw:
        return []
    target = meta.get(HINTS_FOR_KEY)
    if target and running and str(target) != running:
        return []
    items = raw if isinstance(raw, list) else [raw]
    return [str(h).strip() for h in items if str(h or "").strip()][:_HINTS_MAX]


def prompt_addendum(project: Any, step_code: str, running: str | None) -> str:
    """Блоки в конец сообщения модели: ошибка прошлого запуска и замечания ревьюера."""
    blocks: list[str] = []
    fb = step_feedback(project, step_code, running)
    if fb:
        blocks.append(f"{FEEDBACK_HEADER}\n{fb}")
    hints = review_hints(project, running)
    if hints:
        blocks.append(REVIEW_HEADER + "\n" + "\n".join(f"- {h}" for h in hints))
    return ("\n\n" + "\n\n".join(blocks)) if blocks else ""


def clear_review_hints(project: Any, running: str | None) -> bool:
    """После успешного шага замечания использованы: следующий запуск идёт без них."""
    meta = dict(_meta(project))
    if HINTS_KEY not in meta:
        return False
    target = meta.get(HINTS_FOR_KEY)
    if target and running and str(target) != running:
        return False
    meta.pop(HINTS_KEY, None)
    meta.pop(HINTS_FOR_KEY, None)
    project.meta = meta
    return True
