"""«Найди причину ошибки в ИИ-редакторе сцен» — разбор сбоя ноды, а не чтение доски.

Планировщик брал «сцен» за монтаж и читал инциденты и доску: ответ «причина не установлена»,
хотя отклонённые ответы модели лежат на диске. Если просьба про причину сбоя, а план
только читает, в план добавляется diag.node_failure.
"""

from __future__ import annotations

import re

from app.orchestrator.contracts import PlanStep

_CAUSE = re.compile(r"причин|разбер|почему.*(?:упал|ошиб|слома)|что не так|в ч[её]м проблем")
_FAILURE = re.compile(r"ошиб|упал|слома|сбо[йя]|не работ|проблем|падал")
_PROJECT = re.compile(r"проект\w*\s*(?:№|#)?\s*(\d+)|(\d+)\s*-?\w*\s+проект")


def failure_reads(
    plan: list[PlanStep], goal: str, project_id: int | None, fallback_project: int | None = None
) -> list[PlanStep]:
    text = str(goal or "").lower()
    # Без слов про сбой «найди причину» — продолжение прошлого разбора в этом чате.
    if not (_CAUSE.search(text) and (_FAILURE.search(text) or fallback_project)):
        return plan
    if any(step.mutating or step.paid for step in plan):
        return plan
    if any(step.action == "diag.node_failure" for step in plan):
        return plan
    pid = project_id
    for step in plan:
        if pid is None and isinstance(step.params.get("project_id"), int):
            pid = step.params["project_id"]
    found = _PROJECT.search(text) if pid is None else None
    if found:
        pid = int(found.group(1) or found.group(2))
    if pid is None:
        pid = fallback_project
    if pid is None:
        return plan
    first = PlanStep(
        action="diag.node_failure",
        params={"project_id": pid},
        expected="Почему упала нода: ответы модели, вход и причина.",
    )
    return [first, *plan]
