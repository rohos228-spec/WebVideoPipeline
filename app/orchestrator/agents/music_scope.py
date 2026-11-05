"""«Перемонтируй с музыкой потише»: громкость музыки от текущей, без вопроса «сколько по шкале 0–100».

Планировщик на такую просьбу отдавал пустой план с вопросом, а итог приклеивал «Этого чат
пока не умеет» — хотя настройка сборки ``montage.set_assemble_params(bgm_level)`` есть.
Здесь «потише» / «погромче» без числа — сдвиг на ``STEP`` пунктов от громкости проекта, а
«перемонтируй» / «пересобери» — ещё и монтаж. Подтверждение карточкой остаётся.
"""

from __future__ import annotations

import re
from typing import Any

from app.orchestrator.contracts import PlanStep

STEP = 25
_MUSIC = re.compile(r"музык|музон|фонов\w* (?:трек|звук)|\bbgm\b")
_QUIETER = re.compile(r"тише|потиш|приглуш|убав|поменьше|тихую|тихая|тихо\b")
_LOUDER = re.compile(r"громче|погромч|прибав|побольше|громкую|громкая")
_REMOUNT = re.compile(r"перемонт|пересобер|пересобр|смонтир|собери|монтаж")
# Названо число («до 20», «на 30%») — точное значение ставит планировщик.
_NUMBER = re.compile(r"\d")
_PROJECT = re.compile(r"(?:проект\w*|#)\s*(\d{1,5})|(\d{1,5})\s*(?:-?м\s+)?проект")


def volume_direction(goal: str) -> int:
    """-1 — тише, +1 — громче, 0 — не про громкость музыки или названо число."""
    text = " ".join(str(goal or "").lower().replace("ё", "е").split())
    if not _MUSIC.search(text) or _NUMBER.search(_PROJECT.sub(" ", text)):
        return 0
    if _QUIETER.search(text):
        return -1
    if _LOUDER.search(text):
        return 1
    return 0


def shifted_level(current: int, direction: int) -> int:
    return max(0, min(100, int(current) + STEP * direction))


async def _current_level(project_id: int) -> int:
    from sqlalchemy import select

    from app.models import Project
    from app.services.action_layer import store
    from app.services.bgm import _meta_level

    async with store.sessions()() as session:
        meta: Any = await session.scalar(select(Project.meta).where(Project.id == int(project_id)))
    return _meta_level(meta if isinstance(meta, dict) else {})


async def music_volume_plan(goal: str, project_id: int | None) -> list[PlanStep] | None:
    direction = volume_direction(goal)
    named = _PROJECT.search(str(goal or "").lower())
    if named:
        project_id = int(named.group(1) or named.group(2))
    if not direction or project_id is None:
        return None
    try:
        current = await _current_level(int(project_id))
    except Exception:
        return None
    new = shifted_level(current, direction)
    if new == current:
        return None
    word = "тише" if direction < 0 else "громче"
    plan = [
        PlanStep(
            action="montage.set_assemble_params",
            params={"project_id": int(project_id), "bgm_level": new},
            mutating=True,
            risk="medium",
            expected=f"Музыка {word}: громкость {current} → {new} из 100 для следующей сборки.",
        )
    ]
    if _REMOUNT.search(str(goal or "").lower()):
        plan.append(
            PlanStep(
                action="montage.run_montage",
                params={"project_id": int(project_id)},
                mutating=True,
                risk="high",
                expected="Ролик собран заново с новой громкостью музыки.",
            )
        )
    return plan
