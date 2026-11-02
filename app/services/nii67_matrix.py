"""Кадры НИИ67 по полям матрицы двери — без GPT (кнопки «Собрать кадры» / «Матрица»).

Студия (web/src/lib/nii67.ts) хранит у двери ход (`move`), действие существа
(`creature_action`) и поля состояния (`state`). Серверной части у заказчика не было:
кнопка звала /nii67/matrix-plan и получала 404. Здесь поля матрицы превращаются в
обычные блоки двери, а кадры собирает тот же детерминированный сборщик
`nii67_agent.generate` (камеры, проверка кадра, длительности), что и раскадровка.

Дверь без выбранного хода и без своих блоков — черновик: кадры показываются, но не
сохраняются (`preview`), и в `issues` сказано, что заполнить.
"""

from __future__ import annotations

import copy
from typing import Any

from app.services.nii67_agent import generate
from app.services.nii67_card import MATRIX_BLOCK_KINDS, new_block, normalize_card

_DOOR_RU = {"door1": "Дверь 1", "door2": "Дверь 2"}


def _block(kind: str, text: str, *, creature: dict[str, Any] | None = None, **extra: str) -> dict[str, Any]:
    b = new_block(kind)
    b["text"] = text
    for k, v in extra.items():
        b[k] = v
    if creature is not None:
        b["character"] = {**b["character"], **creature, "enabled": True}
    return b


def _first(*values: str) -> str:
    for v in values:
        if v and v.strip():
            return v.strip()
    return ""


def _door_creature(door: dict[str, Any]) -> dict[str, Any]:
    for b in door.get("blocks") or []:
        ch = b.get("character") or {}
        if ch.get("enabled") or b.get("kind") == "creature":
            return {k: v for k, v in ch.items() if k != "enabled"}
    return {"name": "существо"}


def matrix_blocks(door: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    """Блоки двери из хода и полей состояния; второй элемент — чего не хватает."""
    move = door.get("move") or ""
    st = door.get("state") or {}
    name = _DOOR_RU.get(door.get("id"), door.get("id") or "дверь")
    issues: list[str] = []
    world = _first(st.get("world_text"), st.get("world_description"), door.get("theme"))
    env = _first(st.get("environment"))
    blocks = [_block("world_frame", " ".join(x for x in (world, env) if x) or "мир за дверью")]
    if not world:
        issues.append(f"{name}: не задано, что за мир («Что за мир»)")
    if move == "death_world":
        action = _first(st.get("death_action"))
        death = _first(
            st.get("death_text"),
            " ".join(x for x in (st.get("death_cause"), st.get("death_why")) if x),
        )
        blocks.append(_block("gg_action", action or "герой пытается выбраться"))
        blocks.append(_block("enter_action", death or "мир убивает героя"))
        blocks.append(_block("exit", _first(st.get("illustration"), st.get("death_trace")) or "след на месте"))
        if not action:
            issues.append(f"{name}: не задано «Действие перед смертью»")
        if not death:
            issues.append(f"{name}: не задано, от чего герой умер")
    elif move == "neutral":
        place = _first(st.get("place"))
        blocks.append(_block("gg_action", f"герой проходит дальше: {place}" if place else "герой проходит дальше"))
        blocks.append(_block("exit", _first(st.get("illustration"), place) or "герой ушёл дальше"))
        if not place:
            issues.append(f"{name}: не задано «Куда пришёл»")
    elif move == "creature":
        act = door.get("creature_action") or ""
        creature = _door_creature(door)
        blocks.append(_block("world_character", "появляется существо", creature=creature))
        if act == "kill":
            blocks.append(_block("character_action", "существо убивает героя", creature=creature))
            blocks.append(_block("exit", _first(st.get("illustration"), st.get("death_trace")) or "след на месте"))
        elif act == "run":
            blocks.append(_block("character_action", "существо бросается на героя", creature=creature))
            blocks.append(_block("gg_action", "герой убегает обратно к двери"))
        elif act == "interact":
            result = _first(st.get("interaction_result"))
            blocks.append(_block("character_action", result or "существо вступает в контакт", creature=creature))
            if not result:
                issues.append(f"{name}: не задан «Результат взаимодействия»")
        else:
            issues.append(f"{name}: выберите, что делает существо")
    elif move == "scout_exit":
        blocks.append(_block("gg_action", "герой оглядывается и возвращается обратно в дверь"))
    else:
        return [], [f"{name}: не выбран ход двери"]
    return blocks, issues


def matrix_plan(card: Any) -> dict[str, Any]:
    """{"card", "frames", "preview", "issues", "agent"}: кадры по матрице без модели."""
    c = normalize_card(card)
    work = copy.deepcopy(c)
    issues: list[str] = []
    preview = False
    for door in work["doors"]:
        own = [b for b in door["blocks"] if b.get("kind") not in MATRIX_BLOCK_KINDS and (b.get("text") or "").strip()]
        blocks, missing = matrix_blocks(door)
        if not blocks:
            if own:
                continue  # ход не выбран, но дверь расписана своими блоками — берём их
            preview = True
            issues.extend(missing)
            door["blocks"] = [_block("world_frame", door.get("theme") or "мир за дверью")]
            continue
        if missing:
            preview = True
            issues.extend(missing)
        door["blocks"] = blocks
    agent = generate(work)
    issues.extend(agent.get("errors") or [])
    return {"card": c, "frames": agent["frames"], "preview": preview, "issues": issues, "agent": agent}
