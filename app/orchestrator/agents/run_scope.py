"""Короткие команды запуска: «перемонтируй», «доведи до конца».

Планировщик на них отдавал пустой план, и чат отвечал «Этого чат пока не умеет», хотя
«собери заново» в том же проекте давал карточку монтажа. Здесь план без модели: монтаж —
``montage.run_montage``, «до конца» — очередь генерации всего конвейера. Подтверждение
карточкой остаётся: план только предлагает.
"""

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
        return None  # музыка и голос — music_scope / voice_plan
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
                expected=f"Проект #{pid} смонтирован заново: озвучка и сборка ролика.",
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


# «Создай ролик про акул и доведи до картинок»: номера проекта до создания нет, поэтому план —
# создать проект, а итог говорит, какой фразой довести его дальше. Раньше было «чат не умеет».
_CREATE_AND_RUN = re.compile(
    r"^(?:создай|сделай|запили)\s+(?:новый\s+)?(?:ролик|проект|видео)\s+(?:про|о|об)\s+(.+?)\s+и\s+"
    r"(?:доведи|запусти|прогони|сделай)\s+(?:его\s+)?(до\s+[а-я ]+|полностью|целиком|до\s+конца)\s*[.!]*$"
)
# «Сделай отчёт за неделю»: проекты и траты за 7 дней, а не «уточните, какой отчёт».
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
    """«Выгрузи траты в эксель»: отчёт по 33 проектам падал на лимите 20 — берём последние 20."""
    out = []
    for step in plan:
        ids = step.params.get("project_ids") if step.action == "report.projects" else None
        if isinstance(ids, list) and len(ids) > 20:
            params = {k: v for k, v in step.params.items() if k != "project_ids"}
            step = step.model_copy(update={"params": params})
        out.append(step)
    return out
