"""Простые чтения («покажи все проекты», «статус проекта 7»): план из одного шага без модели.

Модель на такие просьбы думала около минуты и ставила тот же ``projects.overview`` или
``project.status``, а потом ещё полминуты пересказывала готовый текст шага. Здесь вся реплика
целиком должна быть такой просьбой: любое лишнее слово («и запусти», «почему», «кроме») —
None, план строит модель как раньше.
"""

from __future__ import annotations

import re
from typing import Any

OVERVIEW = "projects.overview"
STATUS = "project.status"
READ_ACTIONS = frozenset({OVERVIEW, STATUS})

_POLITE = r"(?:пожалуйста\s*,?\s*)?"
_SHOW = r"(?:покажи|выведи|дай|скинь|перечисли|открой|напиши)(?:те)?\s+(?:мне\s+)?"
_PROJECTS = r"проект(?:ы|ов)"
_LIST_RE = re.compile(
    "^"
    + _POLITE
    + r"(?:"
    + r"(?:"
    + _SHOW
    + r")?(?:список\s+)?(?:всех\s+|все\s+)?(?:мои[хм]?\s+)?"
    + _PROJECTS
    + r"(?:\s+списком)?"
    + r"|какие\s+(?:у\s+меня\s+)?(?:есть\s+)?(?:вообще\s+)?"
    + _PROJECTS
    + r"(?:\s+есть)?"
    + r"|сколько\s+(?:у\s+меня\s+)?(?:всего\s+)?"
    + _PROJECTS
    + r"(?:\s+есть)?"
    + r")"
    + r"(?:\s*,?\s*пожалуйста)?$"
)
_RUNNING_RE = re.compile(
    "^"
    + _POLITE
    + r"(?:какие\s+"
    + _PROJECTS
    + r"\s+(?:сейчас\s+)?(?:идут|запущены|в\s+работе|генерируются)"
    + r"|(?:"
    + _SHOW
    + r")?(?:все\s+)?(?:запущенные|идущие|активные)\s+"
    + _PROJECTS
    + r")(?:\s+сейчас)?(?:\s*,?\s*пожалуйста)?$"
)
_PID = r"(?:№\s*|#\s*)?(\d{1,6})"
_STATUS_RE = re.compile(
    "^"
    + _POLITE
    + r"(?:"
    + r"(?:"
    + _SHOW
    + r"|какой\s+|скажи\s+)?статус\s+(?:у\s+)?(?:проекта\s+)?"
    + _PID
    + r"|что\s+(?:сейчас\s+)?(?:с|по)\s+проект(?:ом|у)\s+"
    + _PID
    + r"|(?:где|как)\s+(?:там\s+|сейчас\s+)?проект\s+"
    + _PID
    + r")(?:\s+сейчас)?(?:\s*,?\s*пожалуйста)?$"
)


def _norm(text: str) -> str:
    low = (text or "").lower().replace("ё", "е")
    low = re.sub(r"[?!.…]+", " ", low)
    return " ".join(low.split()).strip(" ,")


def read_step(text: str) -> dict[str, Any] | None:
    """Шаг плана для простого чтения; None — просьба не только про это."""
    low = _norm(text)
    if not low:
        return None
    if _RUNNING_RE.match(low):
        return _step(OVERVIEW, {"status": "running"}, "список идущих проектов")
    if _LIST_RE.match(low):
        return _step(OVERVIEW, {"limit": 200}, "список проектов со статусами")
    m = _STATUS_RE.match(low)
    if m:
        pid = next(g for g in m.groups() if g)
        return _step(STATUS, {"project_id": int(pid)}, "статус проекта")
    return None


def is_simple_read(text: str) -> bool:
    return read_step(text) is not None


def _step(action: str, params: dict[str, Any], expected: str) -> dict[str, Any]:
    return {
        "action": action,
        "params": params,
        "expected": expected,
        "paid": False,
        "mutating": False,
        "risk": "none",
    }
