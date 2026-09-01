"""Инварианты реестра персонажей: один ``cNN`` — одно тело.

**Зачем.** Правило держится текстом в мастер-промтах, и на нём стоит вся
раздача фотореференсов (`frame_cast.reference_character`: фотография уходит
ПЕРВОМУ коду в кадре) и правило «описывать всех из ``describe_appearance``».
Но кодом оно не проверялось нигде, и живой прогон 2026-08-31 показал, чем это
кончается: сцен-агент положил в ``c02`` «трое взрослых круглых существ», а
дальше кадр получил одну фотографию и расстановку «трое круглых в правой
половине». Генератор нарисовал бы трёх клонов одного тела — брак, который
виден только на готовых картинках, то есть после оплаченных генераций.

**Почему предупреждение, а не отказ.** Карточки пишет модель, и жёсткий отказ
на живом проекте остановил бы конвейер там, где человек ещё может поправить
формулировку. Поэтому инвариант шумит в лог и складывается в
``project.meta``, откуда его видно проверкам и интерфейсу.

Вторая половина инварианта — ссылочная: каждый ``cNN``, упомянутый в
расстановке кадра (``attrs.continuity``), обязан существовать в реестре.
Расстановка считается один раз на весь ролик, а реестр потом правят —
разъехаться им ничего не мешает.
"""

from __future__ import annotations

import re
from typing import Any

#: Слова, которыми карточка выдаёт, что описывает НЕ одно тело. Список
#: намеренно узкий: ловим явную множественность, а не любое упоминание группы
#: («стоит среди прохожих» — законное описание одного тела в толпе).
_PLURAL_MARKERS: tuple[str, ...] = (
    "трое",
    "троих",
    "двое",
    "двоих",
    "четверо",
    "пятеро",
    "несколько",
    "группа",
    "все трое",
    "каждый из них",
)

#: «три существа», «два жителя» — числительное + существительное во мн.ч.
_COUNT_RE = re.compile(r"\b(два|две|три|четыре|пять)\s+\w*(существ|жител|персонаж|геро|фигур)", re.IGNORECASE)

_CODE_RE = re.compile(r"\bc\d{1,3}\b", re.IGNORECASE)


def multi_body_problem(code: str, text: str) -> str | None:
    """Описание под одним кодом выглядит как несколько тел → текст проблемы."""
    body = (text or "").strip().lower()
    if not body:
        return None
    for marker in _PLURAL_MARKERS:
        if marker in body:
            return (
                f"{code}: описание под одним кодом выглядит как несколько тел "
                f"(«{marker}»). Один cNN — одно тело: заведи отдельные коды, "
                f"иначе фотореференс уйдёт одному, а промт попросит нескольких"
            )
    m = _COUNT_RE.search(body)
    if m:
        return (
            f"{code}: описание под одним кодом выглядит как несколько тел "
            f"(«{m.group(0)}»). Один cNN — одно тело"
        )
    return None


def codes_in_text(text: str) -> list[str]:
    """Все ``cNN`` из текста, в порядке появления, без дублей и в нижнем регистре."""
    out: list[str] = []
    for raw in _CODE_RE.findall(text or ""):
        code = raw.lower()
        if code not in out:
            out.append(code)
    return out


def dangling_codes(continuity: str, known_codes: set[str]) -> list[str]:
    """Коды из расстановки, которых нет в реестре."""
    known = {str(c).strip().lower() for c in known_codes}
    return [c for c in codes_in_text(continuity) if c not in known]


def check_cast_cards(cards: list[dict[str, Any]]) -> list[str]:
    """Проблемы реестра: несколько тел под кодом, дубли кодов, пустой код."""
    problems: list[str] = []
    seen: set[str] = set()
    for card in cards:
        code = str((card or {}).get("code") or "").strip().lower()
        if not code:
            problems.append("карточка персонажа без кода cNN")
            continue
        if code in seen:
            problems.append(f"{code}: код встречается больше одного раза в реестре")
        seen.add(code)
        raw_attrs = card.get("attrs")
        attrs: dict[str, Any] = raw_attrs if isinstance(raw_attrs, dict) else {}
        text = " ".join(
            str(attrs.get(k) or "") for k in ("look", "внешность", "clothes", "одежда", "rules", "правила")
        )
        problem = multi_body_problem(code, text)
        if problem:
            problems.append(problem)
    return problems


def too_many_characters(codes: list[str], slots: int) -> str | None:
    """В кадре больше персонажей, чем у провайдера слотов референса.

    Правило не эстетическое. Фотореференсов провайдер принимает ровно
    ``slots`` штук (`generation_options.ref_slots_for_provider`), остальных
    в кадре держит только текстовое описание — и они плывут. Живой прогон
    2026-08-31: у героя с единственным рефом лицо всё равно уехало в трёх
    кадрах из тридцати трёх; у персонажа вовсе без рефа шансов нет.

    Поэтому число персонажей в кадре — не «сколько захотелось сцене», а
    свойство контура генерации. Сцену на троих физически можно снять двумя
    кадрами по двое, и это дешевле, чем ловить клонов на приёмке.
    """
    n = len(codes)
    if slots < 1 or n <= slots:
        return None
    return (
        f"персонажей в кадре {n} ({', '.join(codes)}), а слотов референса у "
        f"провайдера {slots}: лишних держит только текст, и они поплывут. "
        f"Раздели на кадры по {slots}"
    )


def frames_over_ref_slots(frames_codes: dict[int, list[str]], slots: int) -> list[tuple[int, str]]:
    """Кадры, где персонажей больше, чем слотов. ``[(номер, проблема), …]``."""
    out: list[tuple[int, str]] = []
    for number in sorted(frames_codes):
        problem = too_many_characters(frames_codes[number], slots)
        if problem:
            out.append((number, problem))
    return out
