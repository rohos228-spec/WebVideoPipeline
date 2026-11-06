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


# «Сделай все картинки в стиле аниме», «картинки темнее», «добавь в каждый кадр луну», «убери из
# промтов кровь»: правка для всех картинок проекта — требование в промт «Промты картинок».
# «Перепиши текст попроще», «текст озвучки длинный, сократи»: требование в промт «Сценарий».
_WIDE_VERB = re.compile(
    r"(?<![а-я])(?:сделай|сделать|добавь|добавить|убери|убрать|поменяй|поменять|смени|пусть|хочу|"
    r"переделай|перепиши|переписать|сократи|сократить|упрости|сделайте|добавьте|уберите|переведи|"
    r"перевести)(?![а-я])"
)
_RUN_WORDS = re.compile(r"запуст|сгенер|генер|перегенер|нарисуй|дорисуй|отрисуй|прогони|пересобер|перезапуст|переозвуч")
_IMAGE_OBJ = re.compile(r"картин|кадр|промт|промпт|изображ|стил|рисун|арт")
_TEXT_OBJ = re.compile(r"текст|озвучк|сценари|закадр|диктор")
_FRAME_NO = re.compile(
    r"кадр\w*\s*(?:(?:№|номер)\s*)?\d|\d+\s*(?:-?(?:й|м|го|ом))?\s*кадр|(?:перв|втор|трет|четверт|пят|последн|двух|трех|четырех|пяти|одн)\w*\s+(?:[а-я]+\s+)?кадр"
    r"|кадр\w*\s+(?:перв|втор|трет|последн)|сцен\w*\s*\d|(?:перв|втор|трет|последн)\w*\s+сцен"
)
# Слова, после которых просьба пустая: «сделай картинки» — это запуск, а не правка.
_FILLER = re.compile(
    r"(?<![а-я])(?:сделай|сделать|добавь|добавить|убери|убрать|поменяй|поменять|смени|пусть|хочу|"
    r"переделай|перепиши|переписать|сократи|сократить|упрости|чтобы|чтоб|все|всех|во|в|на|для|и|"
    r"картинки|картинках|картинок|картинке|кадры|кадрах|кадре|кадров|кадра|каждый|каждом|каждого|всем|промты|промтах|промтов|"
    r"изображения|проект\w*|#?\d+|мне|пожалуйста|ну|давай|были|был|была|было|текст|озвучки|"
    r"озвучку|сценарий|сценария)(?![а-я])"
)


# «Сделай видео 60 секунд», «сделай на английском»: хронометраж и язык задаёт текст сценария.
_SCRIPT_WISH = re.compile(
    r"(?:видео|ролик)\w*\s+(?:на\s+|в\s+|длиной\s+)?\d{1,3}\s*(?:секунд|сек|минут|мин)"
    r"|(?:на|по)[-\s](?:английск|русск|испанск|немецк|французск|итальянск|китайск|англ)\w*"
    r"|переведи|перевести"
)


# «Поставь другую музыку, повеселее»: настроение трека задаёт промт ноды «Музыка».
_MUSIC_MOOD = re.compile(
    r"весел|грустн|спокойн|бодр|энергичн|динамичн|медленн|быстр|друг\w*\s+музык|жанр|рок|джаз|"
    r"электрон|классическ|эпичн|нежн|мрачн|страшн|торжествен|лиричн"
)


def wide_edit_target(goal: str) -> str | None:
    """«images» / «script» — правка для всех картинок или всего текста проекта; иначе None."""
    text = " ".join(str(goal or "").lower().replace("ё", "е").split())
    if _SCRIPT_WISH.search(text) and _WIDE_VERB.search(text) and not _QUESTION.search(text):
        return "script"
    if "музык" in text and _MUSIC_MOOD.search(text) and not _QUESTION.search(text) and not text.endswith("?"):
        return "music"
    if not _WIDE_VERB.search(text) or _QUESTION.search(text) or text.endswith("?"):
        return None
    if _RUN_WORDS.search(text) or _FRAME_NO.search(text):
        return None  # запуск или правка одного кадра — свои пути
    if any(w in text for w in ("музык", "громк", "голос", "диктор", "субтит", "заголов", "назван", "секунд", "длит",
                               "формат", "вертикал", "горизонтал", "разрешен", "переход", "склейк",
                               "монтаж", "титр", "логотип", "обрез", "медленн", "быстрее", "скорост",
                               "тише", "громче")):
        return None
    leftover = _FILLER.sub(" ", text)
    if len(re.findall(r"[а-яa-z]{3,}", leftover)) == 0:
        return None
    if _IMAGE_OBJ.search(text) and not _TEXT_OBJ.search(text):
        return "images"
    if _TEXT_OBJ.search(text) and not _IMAGE_OBJ.search(text):
        return "script"
    return None


async def _node_of_type(project_id: int, node_type: str) -> str | None:
    from sqlalchemy import select

    from app.models import Project
    from app.orchestrator.agents.project_nodes import roster
    from app.services.action_layer import store

    async with store.sessions()() as session:
        meta: Any = await session.scalar(select(Project.meta).where(Project.id == int(project_id)))
    keys = [row["key"] for row in roster(meta if isinstance(meta, dict) else {}) if row["type"] == node_type]
    return keys[0] if keys else None


async def wide_edit_plan(goal: str, project_id: int | None) -> list[PlanStep] | None:
    target = wide_edit_target(goal)
    named = _PROJECT.search(str(goal or "").lower())
    if named:
        project_id = int(named.group(1) or named.group(2))
    if target is None or project_id is None:
        return None
    node_type = {"images": "image_prompts", "script": "script", "music": "music"}[target]
    try:
        node_key = await _node_of_type(int(project_id), node_type)
    except Exception:
        return None
    if not node_key:
        return None
    wish = " ".join(str(goal or "").split()).strip().rstrip(".")
    if target == "images":
        append = (
            f"ТРЕБОВАНИЕ ЧЕЛОВЕКА КО ВСЕМ КАРТИНКАМ ПРОЕКТА: «{wish}». Учти его в каждом промте "
            "картинки. Оно важнее любого другого требования выше."
        )
        title = "Промты картинок"
        after = "Новые промты и картинки появятся после перезапуска этих шагов."
    elif target == "music":
        append = f"ТРЕБОВАНИЕ ЧЕЛОВЕКА К МУЗЫКЕ РОЛИКА: «{wish}». Подбери трек с учётом этого требования."
        title = "Музыка"
        after = "Новый трек появится после перезапуска шага «Музыка» (платно) и пересборки ролика."
    else:
        append = (
            f"ТРЕБОВАНИЕ ЧЕЛОВЕКА К ТЕКСТУ: «{wish}». Пиши закадровый текст с учётом этого требования."
        )
        title = "Сценарий"
        after = "Новый текст появится после перезапуска сценария; шаги ниже него придётся пройти заново."
    return [
        PlanStep(
            action="prompt.edit_text",
            params={
                "project_id": int(project_id),
                "node_key": node_key,
                "rationale": f"Человек попросил: «{wish}».",
                "append": append,
            },
            mutating=True,
            risk="medium",
            expected=f"Готова правка промта «{title}» проекта #{project_id}: «{wish}». {after}",
        )
    ]


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
    if not ("голос" in text or "диктор" in text) or not _VOICE_CHANGE.search(text) or _QUESTION.search(text):
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
