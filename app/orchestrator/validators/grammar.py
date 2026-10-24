"""Предикаты R-MERGE-TRIVIAL и R-WIDE-BEFORE-DIALOG.

Правила — ``app.services.scene_shot_grammar.merge_trivial_shots`` /
``ensure_wide_before_dialogue``. Валидатор всегда зовёт их на deepcopy
и наружу список кадров не меняет.
"""

from __future__ import annotations

import copy
from typing import Any

from app.orchestrator.validators.activity_flags import shot_is_active


def shot_counts_as_active(shot: Any) -> bool:
    """Живой шот. Единственный предикат — ``activity_flags.shot_is_active``."""

    return shot_is_active(shot)


class KadryInvalid:
    """Ключ кадров есть, но значение не список. Это не ``None`` и не ``[]``."""

    invalid = True

    def __repr__(self) -> str:
        return "KadryInvalid()"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, KadryInvalid)

    def __hash__(self) -> int:
        return 0

    def __bool__(self) -> bool:
        return False

    def __iter__(self) -> Any:
        return iter(())


KADRY_INVALID = KadryInvalid()


def is_kadry_invalid(value: Any) -> bool:
    return isinstance(value, KadryInvalid)


def active_shots(shots: list[Any] | None) -> list[Any] | None:
    """Оставить живые шоты. None — списка не было. Сами объекты не меняются."""

    if shots is None or is_kadry_invalid(shots):
        return None if shots is None else shots  # type: ignore[return-value]
    return [shot for shot in shots if shot_counts_as_active(shot)]


def _apply(fn: Any, shots: Any) -> list[str]:
    if not isinstance(shots, list):
        return []
    clone = copy.deepcopy(shots)
    notes = fn(clone)
    return [str(item) for item in (notes or [])]


def merge_notes(shots: Any) -> list[str]:
    from app.services.scene_shot_grammar import merge_trivial_shots

    return _apply(merge_trivial_shots, shots)


def wide_notes(shots: Any) -> list[str]:
    from app.services.scene_shot_grammar import ensure_wide_before_dialogue

    return _apply(ensure_wide_before_dialogue, shots)


def shots_of(context: dict[str, Any]) -> list[Any] | None | KadryInvalid:
    """Кадры из context. None — ключа не было.

    Несписок (строка, dict, null) — ``KadryInvalid``, не ``[]``.
    Неактивные (``shot_is_active`` ложен) в проверку не входят и из context не
    выкидываются: наружу уходит новый список.
    """

    if "shots" in context:
        value = context.get("shots")
        if not isinstance(value, list):
            return KADRY_INVALID
        raw: list[Any] | None = value
    else:
        raw = None
        invalid = False
        after = context.get("after")
        if isinstance(after, dict):
            for key in ("shots", "frames", "кадры"):
                if key not in after:
                    continue
                if isinstance(after[key], list):
                    raw = after[key]
                    break
                invalid = True
        if raw is None:
            for key in ("frames", "кадры"):
                if key not in context:
                    continue
                if isinstance(context[key], list):
                    raw = context[key]
                    invalid = False
                    break
                invalid = True
        if raw is None and invalid:
            return KADRY_INVALID
    return active_shots(raw)
