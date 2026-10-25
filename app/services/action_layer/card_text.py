"""Тексты карточки подтверждения для человека.

Проверки и последствия действий пишутся для планировщика и журнала: в них коды действий
(``queue.set_parallel``), ключи нод (``n_images``), id моделей (``gpt_image_2_vip``) и суммы
с четырьмя знаками. В карточку они идут через ``card_ru``: коды заменяются названиями,
служебные пометки в скобках убираются, суммы округляются до центов.
"""

from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from app.services.plain_ru import plain_ru

_ACTION_ID = re.compile(r"(?<![\w.])[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*(?![\w.])")
# «(excel_gpt с node_key)», «(n_excel_gpt_2)»: пометка для планировщика, человеку она не нужна.
# Убираем только пометки с ключом ноды или id модели: «формат (mp4)» человеку полезен.
_CODE_PAREN = re.compile(r"\s*\((?:[a-z][a-z0-9_]*)(?:\s+(?:с|и|или)\s+[a-z][a-z0-9_]*)*\)")
_CODE_PAREN_MARK = re.compile(r"(?<![\w.])(?:n_[a-z0-9_]+|node_key|model_id)(?![\w.])")
_NODE_KEY = re.compile(r"(?<![\w.])(['\"]?)(n_[a-z0-9_]+)\1(?![\w.])")
_NODE_PARTS = re.compile(r"n_([a-z_]+?)(?:_(\d+))?")
_SNAKE = re.compile(r"(?<![\w.])[a-z][a-z0-9]*(?:_[a-z0-9]+)+(?![\w.])")
_MONEY = re.compile(r"\$(\d+\.\d{3,})(?!\d)")
_PROJECTS = re.compile(r"(\d+) проект\(ов\)")


def projects_ru(count: int) -> str:
    tail = count % 100
    if 11 <= tail <= 14:
        word = "проектов"
    elif count % 10 == 1:
        word = "проект"
    elif 2 <= count % 10 <= 4:
        word = "проекта"
    else:
        word = "проектов"
    return f"{count} {word}"


def money_ru(usd: float) -> str:
    """Сумма до центов; меньше цента — так и сказано, а не «$0.00»."""
    if 0 < usd < 0.005:
        return "меньше $0.01"
    # Через Decimal: у float $1.005 лежит как 1.00499… и округлился бы вниз.
    return "$" + str(Decimal(str(usd)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def node_key_ru(key: str, labels: dict[str, str] | None = None) -> str:
    """Название ноды по ключу: из подписей проекта, иначе по типу из ключа (n_excel_gpt_2).

    Незнакомый ключ (n_excel_gpt_fw_shots) возвращается как есть: в карточке необратимого
    действия человек должен видеть, какую ноду оно меняет, пусть и кодом.
    """
    label = str((labels or {}).get(key) or "").strip()
    if label:
        return label
    from app.services.action_layer.builtin_insight import _TYPE_RU

    found = _NODE_PARTS.fullmatch(key)
    if found and found.group(1) in _TYPE_RU:
        number = found.group(2)
        return _TYPE_RU[found.group(1)] + (f" {number}" if number else "")
    return key


def generator_ru(model_id: str) -> str:
    try:
        from app.generation_options import IMAGE_GENERATORS_BY_ID, VIDEO_GENERATORS_BY_ID
    except Exception:
        return ""
    choice = IMAGE_GENERATORS_BY_ID.get(model_id) or VIDEO_GENERATORS_BY_ID.get(model_id)
    return choice.label.lstrip("+ ").strip() if choice is not None else ""


def _action_title(action_id: str) -> str:
    try:
        from app.services.action_layer.spec import ensure_builtin_actions, registry

        ensure_builtin_actions()
        spec = registry.find(action_id)
    except Exception:
        return ""
    return str(getattr(spec, "title_ru", "") or "").strip()


def card_ru(text: Any, labels: dict[str, str] | None = None) -> str:
    out = plain_ru(text)
    out = _PROJECTS.sub(lambda m: projects_ru(int(m.group(1))), out)
    out = _CODE_PAREN.sub(lambda m: "" if _CODE_PAREN_MARK.search(m.group(0)) else m.group(0), out)

    def _node(match: re.Match[str]) -> str:
        return f"«{node_key_ru(match.group(2), labels)}»"

    out = _NODE_KEY.sub(_node, out)

    def _action(match: re.Match[str]) -> str:
        title = _action_title(match.group(0))
        return f"«{title}»" if title else match.group(0)

    out = _ACTION_ID.sub(_action, out)

    def _model(match: re.Match[str]) -> str:
        name = generator_ru(match.group(0))
        return f"«{name}»" if name else match.group(0)

    out = _SNAKE.sub(_model, out)
    out = _MONEY.sub(lambda m: money_ru(float(m.group(1))), out)
    # Код уже стоял в кавычках или рядом с тем же названием: кавычки и повтор не удваиваем.
    out = re.sub(r"««([^«»]+)»»", r"«\1»", out)
    out = re.sub(r"«([^«»]+)»\s*«\1»", r"«\1»", out)
    return " ".join(out.split())


def card_list_ru(items: Any, labels: dict[str, str] | None = None) -> list[str]:
    out: list[str] = []
    for item in items if isinstance(items, list) else []:
        text = card_ru(item, labels)
        if text:
            out.append(text)
    return out
