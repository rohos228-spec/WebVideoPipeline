from __future__ import annotations

import re
import time
from contextvars import ContextVar
from typing import Any

_SESSION: ContextVar[str | None] = ContextVar("orch_findings_session", default=None)
_LAST: dict[str, dict[str, Any]] = {}
DIAG_ACTIONS = frozenset({"diag.node_failure", "diag.content_checks", "project.status", "node.output"})
_ONLY_WITH_FIXES = frozenset({"project.status", "node.output"})
_TTL_S = 6 * 3600


def bind(session_id: str | None):
    return _SESSION.set(session_id)


def unbind(token) -> None:
    _SESSION.reset(token)


def current_session() -> str | None:
    return _SESSION.get()


def remember(action: str, params: dict[str, Any], result: Any, session_id: str | None = None) -> None:
    sid = session_id or _SESSION.get()
    if not sid or action not in DIAG_ACTIONS or not isinstance(result, dict):
        return
    fixes = [f for f in (result.get("fix_options") or []) if isinstance(f, dict)]
    if action in _ONLY_WITH_FIXES and not fixes:
        return
    pid = params.get("project_id")
    if not isinstance(pid, int) or isinstance(pid, bool):
        return
    _LAST[sid] = {
        "action": action,
        "params": {k: v for k, v in params.items() if not str(k).startswith("_")},
        "fix_options": fixes,
        "suggested_phrase": result.get("suggested_phrase"),
        "healthy_text": _healthy_text(action, result, fixes),
        "at": time.time(),
    }


def _healthy_text(action: str, result: dict[str, Any], fixes: list[dict[str, Any]]) -> str | None:
    if action != "diag.node_failure" or not result.get("found"):
        return None
    if str(result.get("picked") or "").startswith("последний запуск"):
        return None
    cause = result.get("cause") if isinstance(result.get("cause"), dict) else {}
    if result.get("errors") or result.get("rejects") or str(cause.get("code") or "unknown") != "unknown":
        return None
    title = str(result.get("title") or "нода")
    pid = result.get("project_id")
    text = f"По разбору чинить нечего: нода «{title}» завершилась без ошибки, отклонённых ответов и ошибок нет."
    if any(f.get("action") == "step.run_step" for f in fixes) and isinstance(pid, int):
        text += f" Если нужно пересобрать её результат — «перезапусти {title.lower()} проекта {pid}»."
    return text


def last(session_id: str | None) -> dict[str, Any] | None:
    row = _LAST.get(session_id or "")
    if row is None or time.time() - float(row.get("at") or 0) > _TTL_S:
        return None
    return row


def forget(session_id: str | None) -> None:
    _LAST.pop(session_id or "", None)


def next_phrase(fixes: list[dict[str, Any]], applied: list[str]) -> str | None:
    for fix in fixes:
        if fix.get("action") == "step.run_step" and fix.get("phrase_ru") and "step.run_step" not in applied:
            return str(fix["phrase_ru"])
    for fix in fixes:
        if fix.get("then_ru"):
            return str(fix["then_ru"])
    return None


_NEXT: dict[str, str] = {}
_BARE_LAUNCH = re.compile(
    r"^\s*(?:а\s+|ну\s+|и\s+|ок\s+|окей\s+|хорошо\s+)*(?:теперь\s+|потом\s+|тогда\s+|давай\s+)*"
    r"(?:запускай|запусти|запуск|перезапускай|перезапусти|стартуй|поехали|погнали|гони|пускай|пусти)"
    r"(?:\s+(?:его|её|ее|это|теперь|тогда|давай|дальше|заново))*\s*[.!?…]*\s*$"
)


def set_next(session_id: str | None, phrase: str | None) -> None:
    if not session_id:
        return
    if phrase:
        _NEXT[session_id] = str(phrase)
    else:
        _NEXT.pop(session_id, None)


def launch_goal(goal: str, session_id: str | None) -> str:
    phrase = _NEXT.get(session_id or "")
    if phrase and _BARE_LAUNCH.match(str(goal or "").lower().replace("ё", "е")):
        _NEXT.pop(session_id or "", None)
        return phrase
    return goal
