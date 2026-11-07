from __future__ import annotations

import asyncio
import os
import re
from typing import Any

from loguru import logger

COMMANDS = (
    "кадр <N> переделай",
    "в кадре <N> сделай <что изменить на картинке>",
    "перезапусти видео кадра <N>",
    "покажи промт кадра <N>",
    "поменяй промт кадра <N> на <новый промт>",
    "поменяй местами кадры <A> и <B>",
    "поменяй местами видео кадров <A> и <B>",
    "сгенерь картинки только для первых <N> кадров",
    "запусти картинки",
    "запусти видео",
    "запусти только озвучку",
    "запусти до монтажа",
    "доведи до конца",
    "перемонтируй",
    "собери заново",
    "сделай недостающие",
    "дорисуй недостающие видео",
    "продолжи",
    "запусти проект <P> до <картинок|видео|монтажа>",
    "запусти проекты <P1, P2, …> до <картинок|видео|монтажа>",
    "запусти все проекты до <картинок|видео|монтажа>",
    "сделай все картинки в стиле <стиль>",
    "сделай картинки <темнее|светлее|ярче|реалистичнее|…>",
    "добавь в каждый кадр <что>",
    "убери из промтов <что>",
    "перепиши сценарий <как: смешнее, короче, для детей…>",
    "текст озвучки слишком длинный, сократи",
    "сделай видео <N> секунд",
    "сделай на <язык> языке",
    "музыку потише",
    "музыку громче",
    "убери музыку",
    "поставь другую музыку, <настроение>",
    "сделай голос женский",
    "сделай голос мужской",
    "поставь голос <имя>",
    "включи субтитры",
    "выключи субтитры",
    "поставь картинки на <модель>",
    "поменяй модель видео на подешевле",
    "сделай горизонтальное видео",
    "сделай вертикальное видео",
    "создай ролик про <тема>",
    "сделай <N> роликов про <тема>",
    "скопируй проект <P> но про <тема>",
    "переименуй проект <P> в <название>",
    "опубликуй",
    "выложи на <ютуб|тикток|вк|инстаграм>",
    "останови проект <P>",
    "поставь проект <P> на паузу",
    "отмени последнюю правку",
    "откати музыку",
    "выключи автозапуск",
    "включи автозапуск",
)

_SYSTEM = (
    "Ты переводишь просьбу человека в видеостудии в ОДНУ команду из списка. Подставь в <…> "
    "значения из просьбы (номера кадров и проектов — цифрами, «второй» = 2). Если человек назвал "
    "номер проекта, допиши в конце « в проекте P». Если в просьбе несколько действий — выбери "
    "главное. Если это вопрос (узнать, показать состояние, сколько стоит, почему), разговор, "
    "или ни одна команда не подходит по смыслу — ответь ровно NONE. Ничего не придумывай сверх "
    "просьбы. Ответ — только команда или NONE, без кавычек и пояснений.\n\nКоманды:\n"
    + "\n".join(f"- {c}" for c in COMMANDS)
)

REWRITE_TIMEOUT_S = 20.0
ENABLED = os.environ.get("ORCH_INTENT_REWRITE", "1") != "0"
_NONE = re.compile(r"^\W*none\W*$", re.IGNORECASE)


def _clean(text: str) -> str | None:
    line = " ".join(str(text or "").strip().splitlines()[:1]).strip().strip("«»\"'`").strip()
    line = re.sub(r"^[-•*]\s*", "", line)
    if not line or _NONE.match(line) or len(line) > 200:
        return None
    if "<" in line or ">" in line:
        return None
    if line[:1] in "{[" or not re.search(r"[а-яё]", line.lower()):
        return None
    return line


def _looks_question(goal: str) -> bool:
    low = " ".join(str(goal or "").lower().split())
    return low.endswith("?") and not re.search(r"(?<![а-я])(?:можешь|можно|давай|сделай|запусти)(?![а-я])", low)


async def canonical_goal(goal: str, router: Any, kwargs: dict[str, Any]) -> str | None:
    if not ENABLED or router is None or not str(goal or "").strip() or _looks_question(goal):
        return None
    from app.orchestrator.agents.llm_call import router_call

    try:
        response = await asyncio.wait_for(
            router_call(
                router,
                "fast",
                [{"role": "system", "content": _SYSTEM}, {"role": "user", "content": str(goal)}],
                stream=False,
                run_id=kwargs.get("run_id"),
                task_id=kwargs.get("task_id"),
                agent_role="classifier",
                budget_scope=kwargs.get("budget_scope"),
            ),
            REWRITE_TIMEOUT_S,
        )
    except Exception as exc:
        logger.info("orch intent rewrite skipped: {}", type(exc).__name__)
        return None
    out = _clean(getattr(response, "text", "") or "")
    if out and " ".join(out.lower().split()) == " ".join(str(goal).lower().split()):
        return None
    return out
