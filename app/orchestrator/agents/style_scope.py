"""«Сделай, чтобы во всех картинках был пластилин»: стиль картинок — правка промта ноды «Промты картинок».

Планировщик на такую просьбу отдавал пустой план, и чат отвечал «Этого чат пока не умеет».
Стиль картинок проекта задаёт промт ноды «Промты картинок» (вариант промта): здесь просьба
сменить стиль — ``prompt.edit_text`` с дописанным требованием стиля только в этом проекте.
Правка — предложение: применяет человек, откат есть. Новые промты и картинки — отдельным
запуском, о нём говорит итог.
"""

from __future__ import annotations

import re
from typing import Any

from app.orchestrator.contracts import PlanStep

# Как выглядит стиль словами для модели промтов: одно слово «пластилин» модель часто теряла.
STYLE_TEXT = {
    "пластилин": "пластилиновая анимация (claymation, plasticine): герои, предметы и фон вылеплены из "
    "пластилина, видны мягкие формы и следы пальцев, тёплый студийный свет",
    "вязаный": "вязаные игрушки и войлок (knitted, felted): фактура пряжи и войлока, мягкие формы, "
    "уютный рассеянный свет",
    "нуар": "нуар (noir): приглушённая или чёрно-белая палитра, глубокие тени, высокий контраст, "
    "кинематографичный свет",
}
_WORDS = {
    "пластилин": ("пластилин", "claymation", "plasticine"),
    "вязаный": ("вязан", "войлок", "knitted"),
    "нуар": ("нуар", "noir"),
}
_CHANGE = re.compile(
    r"(?<![а-я])(?:сделай|сделать|поменяй|поменять|смени|сменить|переделай|переделать|пусть|хочу|"
    r"нужен|нужна|нужно|поставь|давай)(?![а-я])"
)
_WHERE = re.compile(r"картин|кадр|изображ|стил")
_QUESTION = re.compile(r"^(?:почему|зачем|где|сколько|какой|какие|есть ли)\b|провер")
_PROJECT = re.compile(r"(?:проект\w*|#)\s*(\d{1,5})|(\d{1,5})\s*(?:-?м\s+)?проект")


def wanted_style(goal: str) -> str | None:
    text = " ".join(str(goal or "").lower().replace("ё", "е").split())
    if not _CHANGE.search(text) or not _WHERE.search(text) or _QUESTION.search(text):
        return None
    found = [name for name, words in _WORDS.items() if any(w in text for w in words)]
    return found[0] if len(found) == 1 else None


async def _image_prompts_node(project_id: int) -> str | None:
    from sqlalchemy import select

    from app.models import Project
    from app.orchestrator.agents.project_nodes import roster
    from app.services.action_layer import store

    async with store.sessions()() as session:
        meta: Any = await session.scalar(select(Project.meta).where(Project.id == int(project_id)))
    keys = [
        row["key"] for row in roster(meta if isinstance(meta, dict) else {}) if row["type"] == "image_prompts"
    ]
    return keys[0] if keys else None


async def style_plan(goal: str, project_id: int | None) -> list[PlanStep] | None:
    style = wanted_style(goal)
    named = _PROJECT.search(str(goal or "").lower())
    if named:
        project_id = int(named.group(1) or named.group(2))
    if style is None or project_id is None:
        return None
    try:
        node_key = await _image_prompts_node(int(project_id))
    except Exception:
        return None
    if not node_key:
        return None
    append = (
        f"СТИЛЬ ВСЕХ КАРТИНОК ПРОЕКТА: {STYLE_TEXT[style]}. Пиши этот стиль в каждом промте картинки. "
        "Это требование важнее любого другого стиля, названного выше."
    )
    return [
        PlanStep(
            action="prompt.edit_text",
            params={
                "project_id": int(project_id),
                "node_key": node_key,
                "rationale": f"Человек попросил стиль «{style}» во всех картинках проекта.",
                "append": append,
            },
            mutating=True,
            risk="medium",
            expected=f"Готова правка промта «Промты картинок»: стиль «{style}» во всех картинках. "
            "Новые промты и картинки появятся после перезапуска этих шагов.",
        )
    ]


_VOICE_CHANGE = re.compile(
    r"(?<![а-я])(?:поменяй|поменять|смени|сменить|поставь|поставить|сделай|хочу|нужен|давай|выбери)(?![а-я])"
)


def voice_plan(goal: str, project_id: int | None) -> list[PlanStep] | None:
    """«Поменяй голос на мужской», «поставь голос Сара»: смена голоса ноды «Озвучка»."""
    text = " ".join(str(goal or "").lower().replace("ё", "е").split())
    if "голос" not in text or not _VOICE_CHANGE.search(text) or _QUESTION.search(text):
        return None
    from app.services.action_layer.builtin_voice import voice_keyword

    want = voice_keyword(text)
    if want is None:
        return None
    named = _PROJECT.search(text)
    if named:
        project_id = int(named.group(1) or named.group(2))
    if project_id is None:
        return None
    return [
        PlanStep(
            action="audio.set_voice",
            params={"project_id": int(project_id), "voice": want},
            mutating=True,
            risk="medium",
            expected="Голос озвучки сменён; новая озвучка — после перезапуска шага озвучки.",
        )
    ]
