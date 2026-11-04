"""«Найди причину ошибки в ИИ-редакторе сцен» — разбор сбоя ноды, а не чтение доски.

Планировщик брал «сцен» за монтаж и читал инциденты и доску: ответ «причина не установлена»,
хотя отклонённые ответы модели лежат на диске. Если просьба про причину сбоя, а план
только читает, в план добавляется diag.node_failure.
"""

from __future__ import annotations

import re
from typing import Any

from app.orchestrator.contracts import PlanStep, registry_marks_paid

_CAUSE = re.compile(r"причин|разбер|почему.*(?:упал|ошиб|слома)|что не так|в ч[её]м проблем")
_FAILURE = re.compile(r"ошиб|упал|слома|сбо[йя]|не работ|проблем|падал")
_PROJECT = re.compile(r"проект\w*\s*(?:№|#)?\s*(\d+)|(\d+)\s*-?\w*\s+проект")


_FIX_ONLY = re.compile(
    r"^\s*(?:а\s+)?(?:(?:можешь|давай|ну)\s+)?(?:почини|исправь|исправить|починить|чини|поправь|"
    r"сделай\s+как\s+предлож\w*|сделай\s+это|давай\s+исправ\w*)(?:\s+(?:это|её|ее|его|ноду|ошибку|сбой|"
    r"сам|пожалуйста|тогда|теперь|расхождени\w*|несоответстви\w*|ошибк\w*|все|всё))*\s*[.!?]*\s*$"
)


def is_bare_fix(goal: str) -> bool:
    return bool(_FIX_ONLY.match(str(goal or "").lower()))


def fix_after_diag_goal(goal: str, last_project: int | None) -> str:
    """«Почини» без подробностей сразу после разбора — исправить сбой из того разбора."""
    if last_project is None or not _FIX_ONLY.match(str(goal or "").lower()):
        return goal
    return (
        f"{goal} — исправь сбой ноды в проекте {last_project}, который ты только что разбирал: "
        "сначала diag.node_failure этого проекта без node_key (упавшую ноду он находит сам), потом "
        "ровно одно исправление — первое из его «Что можно сделать»; другие шаги не запускай."
    )


_READ_ONLY = frozenset(
    {"node.output", "logs.tail", "diag.node_failure", "diag.incidents", "diag.content_checks"}
)
_FIX_VERB = re.compile(r"исправ|почин|чини|поправ")
_MAX_EDITS = 8


def _fix_step(fix: dict, default: str) -> PlanStep:
    return PlanStep(
        action=str(fix["action"]),
        params=dict(fix.get("params") or {}),
        expected=str(fix.get("title_ru") or default),
        mutating=True,
    )


async def plan_fix_from_diag(project_id: int) -> list[PlanStep] | None:
    """Первое исправление из свежего разбора сбоя — шагом плана (карточку решает риск действия)."""
    from app.services.orchestrator_autonomy_run import diagnose

    diag = await diagnose(None, int(project_id), "system")
    fixes = (diag or {}).get("fix_options") or []
    for fix in fixes:
        action = fix.get("action")
        if not action or action in _READ_ONLY:
            continue
        return [_fix_step(fix, "Исправление сбоя ноды")]
    return None


async def plan_fix_from_content(project_id: int, step: str | None) -> list[PlanStep] | None:
    """«Почини» после проверки содержания: точные правки ячеек (бесплатно) или первое исправление."""
    from app.orchestrator import app_ref
    from app.services.action_layer.content_checks_diag import _diag_content_checks
    from app.services.action_layer.context import ActionContext

    ctx = ActionContext(app=app_ref.current(), operator="system", read_only=True)
    params: dict = {"project_id": int(project_id), **({"step": step} if step else {})}
    out = await _diag_content_checks(ctx, params)
    fixes = [f for f in (out or {}).get("fix_options") or [] if f.get("action") not in _READ_ONLY]
    # Человек сам попросил починить: правки ячеек закадра — все, карточкой.
    exact = [f for f in fixes if f.get("deterministic") or f.get("action") == "shot.edit_cell_voiceover"]
    if exact:
        return [_fix_step(f, "Правка по проверке содержания") for f in exact[:_MAX_EDITS]]
    if fixes:
        return [_fix_step(fixes[0], "Исправление по проверке содержания")]
    # Исправлять нечего: показать проверку заново, а не отдавать «почини» планировщику.
    return [PlanStep(action="diag.content_checks", params=params, expected="Проверка содержания заново")]


_CHECK = re.compile(r"провер|сверь|сверить|сходит|совпада")
_SHOW = re.compile(r"покажи|выведи|выгрузи|список|таблиц|какие\s+промт")
_PROMPTS = re.compile(r"промт|промпт|prompt|картин|изображ|герои|персонаж")


def _changes(step: PlanStep) -> bool:
    # Отчёт пишет файл и бывает помечен mutating, но доску не меняет.
    return bool(step.paid or (step.mutating and not step.action.startswith("report.")))


def content_check_scope(plan: list[PlanStep], goal: str) -> list[PlanStep]:
    """«Всё ли сходится в кадрах» — разбивка; промты картинок — только если о них спросили.

    Планировщик ставил step=img_pr на вопрос про кадры, и сверка закадра не шла вовсе.
    Без step действие проверяет разбивку и, если есть, промты.
    «Проверь промты картинок» планировщик читал как «покажи промты» — выдавал таблицу;
    просьба проверить при плане из одних чтений — это проверка промтов.
    """
    text = str(goal or "").lower()
    if _PROMPTS.search(text):
        found = _PROJECT.search(text)
        if (
            found
            and _CHECK.search(text)
            and not _SHOW.search(text)
            and plan
            and not any(_changes(step) or step.action == "diag.content_checks" for step in plan)
        ):
            pid = int(found.group(1) or found.group(2))
            return [
                PlanStep(
                    action="diag.content_checks",
                    params={"project_id": pid, "step": "img_pr"},
                    expected="Герои cNN в промтах картинок есть в реестре и совпадают с кадрами",
                )
            ]
        return plan
    out: list[PlanStep] = []
    for step in plan:
        if step.action == "diag.content_checks" and step.params.get("step") == "img_pr":
            step = step.model_copy(update={"params": {k: v for k, v in step.params.items() if k != "step"}})
        out.append(step)
    return out


async def plan_fix_from_last(last: tuple[str, dict] | None) -> list[PlanStep] | None:
    """Исправление из последнего разбора в этом чате: сбой ноды или проверка содержания."""
    if not last:
        return None
    action, params = last
    pid = params.get("project_id")
    if not isinstance(pid, int):
        return None
    if action == "diag.content_checks":
        return await plan_fix_from_content(pid, params.get("step"))
    return await plan_fix_from_diag(pid)


async def fix_with_diag(plan: list[PlanStep], goal: str, registry: Any | None = None) -> list[PlanStep]:
    """«Найди причину и исправь» одной фразой: исправление — из разбора, а не догадка планировщика.

    Планировщик ставил после diag.node_failure перезапуск самой ноды, хотя на входе не было
    кадров: тот же сбой ещё раз. Правки плана заменяются первым исправлением разбора.
    Флаги paid/mutating у черновика ещё не проставлены — платность смотрим по реестру.
    """

    def change(step: PlanStep) -> bool:
        return bool(step.mutating or step.paid or registry_marks_paid(step.action, registry))

    if not _FIX_VERB.search(str(goal or "").lower()):
        return plan
    diag = next((step for step in plan if step.action == "diag.node_failure"), None)
    if diag is None or not isinstance(diag.params.get("project_id"), int):
        return plan
    if not any(change(step) for step in plan):
        return plan
    fixed = await plan_fix_from_diag(diag.params["project_id"])
    if not fixed:
        return plan
    return [*(step for step in plan if not change(step)), *fixed]


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
