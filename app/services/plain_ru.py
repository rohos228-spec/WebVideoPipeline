from __future__ import annotations

import re
from typing import Any

_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"состояния\s+master\s+и\s+project\s+расходятся", re.IGNORECASE),
        "Две базы данных проекта показывают разное",
    ),
    (
        re.compile(r"несовпадение\s+master\s*/\s*project\s+snapshot", re.IGNORECASE),
        "несовпадение двух баз данных проекта",
    ),
    (re.compile(r"master\s*/\s*project\s+snapshot", re.IGNORECASE), "две базы данных проекта"),
    (re.compile(r"(?<![\w.])master\s*/\s*project(?![\w.])", re.IGNORECASE), "две базы данных проекта"),
    (
        re.compile(r"(?<![\w.])master\s+и\s+project(?![\w.])", re.IGNORECASE),
        "основная и проектная базы данных",
    ),
    (
        re.compile(r"(?<![\w.])(?:сделай|сделайте)\s+split(?:_frames)?(?![\w.])", re.IGNORECASE),
        "сделайте разбивку на кадры",
    ),
    (
        re.compile(r"(?<![\w.])сначала\s+split(?:_frames)?(?![\w.])", re.IGNORECASE),
        "сначала нужна разбивка на кадры",
    ),
    (
        re.compile(r"(?<![\w.])split(?:_frames)?\s+не\s+делали(?![\w.])", re.IGNORECASE),
        "разбивку на кадры не делали",
    ),
    (re.compile(r"(?<![\w./])split(?:_frames)?(?![\w./])", re.IGNORECASE), "разбивка на кадры"),
    (re.compile(r"(?<![\w./])snapshots?(?![\w./])", re.IGNORECASE), "снимок"),
    (
        re.compile(r"(?<![\w.])project\.stop\s+или\s+аварийн\w*\s+стоп\w*(?!\w)", re.IGNORECASE),
        "кнопка «Стоп задачи» или аварийный стоп в «Регистраторе»",
    ),
    (re.compile(r"(?<![\w.])project\.stop(?![\w.])", re.IGNORECASE), "кнопка «Стоп задачи»"),
    (re.compile(r"условие\s+[a-z][a-z0-9_]*:\s*"), ""),
    (re.compile(r"(?<![\w.])verify_code_state(?![\w.])"), "проверка состояния"),
    (re.compile(r"(?<![\w.])project\.status(?![\w.])"), "чтение статуса проекта"),
    (re.compile(r"(?<![\w.])node\.describe(?![\w.])"), "чтение ноды"),
    (re.compile(r"(?<![\w.])(?:step|node)\.run_step(?![\w.])"), "запуск шага"),
    (
        re.compile(r"[Уу]кажите ceiling_credits\s*[-—]\s*потолок расхода на этот шаг"),
        "Напишите в чате лимит расхода на этот шаг, например «лимит $1»",
    ),
    (re.compile(r"(?<![\w.])ceiling_credits(?![\w.])"), "лимит расхода"),
)
_STATUS_AFTER = re.compile(r"(статус\w*(?:\s+проекта)?\s+)([a-z][a-z0-9_]*)(?![\w.])")


def _status_word(match: re.Match[str]) -> str:
    from app.models import ProjectStatus
    from app.services.action_layer.builtin_insight import _status_ru

    code = match.group(2)
    try:
        ProjectStatus(code)
    except ValueError:
        return match.group(0)
    return match.group(1) + "«" + _status_ru(code) + "»"


def plain_ru(text: Any) -> str:
    out = "" if text is None else str(text)
    for pattern, replacement in _RULES:
        out = pattern.sub(replacement, out)
    return _STATUS_AFTER.sub(_status_word, out)
