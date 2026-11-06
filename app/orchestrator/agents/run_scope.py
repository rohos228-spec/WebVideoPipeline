from __future__ import annotations

import re

from app.orchestrator.contracts import PlanStep

_NORM = re.compile(r"\s+")
_REMOUNT = re.compile(
    r"^(?:ну\s+|давай\s+|можешь\s+|пожалуйста\s+)*(?:перемонтируй|перемонтировать|пересобери|пересобрать|"
    r"смонтируй|смонтировать|собери|собрать)(?:\s+(?:заново|ещ[её]\s+раз|снова|ролик|видео|его|"
    r"проект\s*#?\d+|в\s+проекте\s*#?\d+|пожалуйста))*\s*[.!]*$"
)
_FULL = re.compile(
    r"^(?:ну\s+|давай\s+|можешь\s+)*(?:доведи|доделай|прогони|догони|сделай|запусти|доделать|довести)"
    r"(?:\s+(?:его|проект\s*#?\d+|\d+(?:\s*проект)?|все|всё))*"
    r"\s+(?:до\s+конца|полностью|целиком|до\s+готового(?:\s+ролика|\s+видео)?|до\s+финала)"
    r"(?:\s+(?:проект\s*#?\d+|\d+))?\s*[.!]*$"
)
_PROJECT = re.compile(r"(?:проект\w*|#)\s*(\d{1,5})|(?<![\w#])(\d{1,5})(?:\s*-?м)?(?:\s+проект|\s*[.!]*$)")


def _text(goal: str) -> str:
    return _NORM.sub(" ", str(goal or "").lower().replace("ё", "е")).strip()


def _project(text: str, project_id: int | None) -> int | None:
    named = _PROJECT.search(text)
    if named:
        return int(named.group(1) or named.group(2))
    return project_id


def run_plan(goal: str, project_id: int | None) -> list[PlanStep] | None:
    text = _text(goal)
    if any(word in text for word in ("музык", "громк", "тише", "голос", "субтит")):
        return None
    if _REMOUNT.match(text):
        pid = _project(text, project_id)
        if pid is None:
            return None
        return [
            PlanStep(
                action="montage.run_montage",
                params={"project_id": int(pid)},
                mutating=True,
                risk="high",
                expected=f"Проект #{pid} смонтирован заново из готовой озвучки и кадров.",
            )
        ]
    if _FULL.match(text):
        pid = _project(text, project_id)
        if pid is None:
            return None
        return [
            PlanStep(
                action="queue.enqueue_many",
                params={"project_ids": [int(pid)], "mode": "full", "enable_auto_mode": True},
                mutating=True,
                risk="high",
                expected=f"Проект #{pid} в очереди генерации: студия сама пройдёт все шаги до готового ролика.",
            )
        ]
    return None


_CREATE_AND_RUN = re.compile(
    r"^(?:создай|сделай|запили)\s+(?:новый\s+)?(?:ролик|проект|видео)\s+(?:про|о|об)\s+(.+?)\s+и\s+"
    r"(?:доведи|запусти|прогони|сделай)\s+(?:его\s+)?(до\s+[а-я ]+|полностью|целиком|до\s+конца)\s*[.!]*$"
)
_WEEK_REPORT = re.compile(r"(?:отчет|сводк|итог)\w*\s+(?:за|по)\s+(?:эту\s+|прошл\w+\s+)?(?:недел|7\s+дн)")


def create_run_plan(goal: str) -> list[PlanStep] | None:
    text = _text(goal)
    found = _CREATE_AND_RUN.match(text)
    if found:
        topic = " ".join(str(goal).split())
        low = topic.lower().replace("ё", "е")
        start = low.find(found.group(1))
        topic = topic[start : start + len(found.group(1))] if start >= 0 else found.group(1)
        until = found.group(2)
        return [
            PlanStep(
                action="project.create",
                params={"title": f"Ролик про {topic}", "topic": f"Ролик про {topic}"},
                mutating=True,
                risk="medium",
                expected=f"Проект про «{topic}» создан. Довести его можно фразой «доведи его {until}»: "
                "в новом проекте это отдельная карточка с ценой.",
            )
        ]
    if _WEEK_REPORT.search(text):
        return [
            PlanStep(action="projects.overview", params={"limit": 30}, mutating=False, risk="none",
                     expected="Проекты студии и их состояние."),
            PlanStep(action="spend.summary", params={}, mutating=False, risk="none",
                     expected="Траты за 7 дней и на что они ушли."),
        ]
    return None


def clip_report_ids(plan: list[PlanStep]) -> list[PlanStep]:
    out = []
    for step in plan:
        ids = step.params.get("project_ids") if step.action == "report.projects" else None
        if isinstance(ids, list) and len(ids) > 20:
            params = {k: v for k, v in step.params.items() if k != "project_ids"}
            step = step.model_copy(update={"params": params})
        out.append(step)
    return out


_RESET_ASKED = re.compile(
    r"(?<![а-я])(?:сброс\w*|сбрось|обнули\w*|очисти\w*|с\s+нуля|с\s+чистого|удали\w*|стереть|сотри)(?![а-я])"
)


def drop_needless_reset(plan: list[PlanStep], goal: str) -> list[PlanStep]:
    text = " ".join(str(goal or "").lower().replace("ё", "е").split())
    if _RESET_ASKED.search(text):
        return plan
    runs = {
        (step.params.get("project_id"), step.params.get("step_code"))
        for step in plan
        if step.action in ("step.run_step", "node.run_step")
    }
    out = [
        step
        for step in plan
        if not (
            step.action == "node.reset_from_step"
            and (step.params.get("project_id"), step.params.get("step_code")) in runs
        )
    ]
    return out if out else plan

_PUBLISH = re.compile(
    r"(?<![а-я])(?:опубликуй|опубликовать|выложи|выкладывай|запости|залей|публикуй)(?![а-я])"
)
_PUBLISH_NOT = re.compile(r"(?<![а-я])(?:как|можно\s+ли|когда|где|почему|куда|не\s+публикуй|не\s+выкладывай)(?![а-я])|\?")


async def publish_plan(goal: str, project_id: int | None) -> list[PlanStep] | None:
    text = " ".join(str(goal or "").lower().replace("ё", "е").split())
    if not _PUBLISH.search(text) or _PUBLISH_NOT.search(text) or len(text.split()) > 10:
        return None
    named = re.search(r"(?:проект\w*|#)\s*(\d{1,5})", text)
    if named:
        project_id = int(named.group(1))
    if project_id is None:
        return None
    from sqlalchemy import select

    from app.models import Project
    from app.services.action_layer import store
    from app.services.action_layer.planner_context import publish_facts
    from app.services.action_layer.planner_rules import named_platforms

    try:
        async with store.sessions()() as session:
            project = await session.scalar(select(Project).where(Project.id == int(project_id)))
    except Exception:
        return None
    if project is None:
        return None
    server = publish_facts(project)
    platforms = named_platforms(goal) or list(server.get("platforms") or [])
    caption = str(server.get("caption") or "")
    if not platforms or not caption:
        return None
    return [
        PlanStep(
            action="publish.send",
            params={"project_id": int(project_id), "platforms": platforms, "caption": caption},
            mutating=True,
            risk="high",
            expected=f"Ролик проекта #{project_id} отправлен на публикацию с подписью «{caption}».",
        )
    ]
