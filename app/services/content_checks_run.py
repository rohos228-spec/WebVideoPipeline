"""Находки проверки содержания → уведомление в чат → исправление сам или карточка.

Вызов — из воркера после успешного шага, когда ``content_checks.check_after_step`` уже
записал отчёт в ``project.meta``. Здесь, в фоне: уведомление чата (ключ — шаг и отпечаток
находок, так что одна и та же картина даёт одно сообщение), решение ``decide_fix`` с теми
же тремя переключателями, что у сбоя ноды, исполнение через слой действий и повторная
проверка с итогом в чат.

Без карточки идут только:

- бесплатные однозначные правки (``deterministic``): закадр ячейки из voiceover.txt,
  канон героя в промте — при ``auto_fix_on_failure``;
- повтор шага (``step.run_step``) — при ``auto_retry``, с потолком расхода и не больше
  ``max_auto_retries_per_node`` раз подряд; модель получает список находок
  (``orchestrator_retry_feedback.txt`` по коду шага, читает ``retry_feedback``).

Пока в проекте идёт шаг, сами ничего не правим: правка ячейки посреди шага затрётся или
собьёт его — только карточка. Ошибки здесь не доходят до конвейера: всё в фоне и под try.
"""

from __future__ import annotations

import asyncio
from typing import Any

from loguru import logger

from app.services import content_checks as cc
from app.services import orchestrator_autonomy as oa

KIND = "content_check"
KIND_FIX = "content_check_fix"
_BUSY: set[tuple[str, int]] = set()
_TASKS: set[asyncio.Task] = set()


def retry_key(step: str) -> str:
    """Счётчик автоповторов по находкам — отдельно от сбоев нод того же проекта."""
    return f"content:{step}"


def on_report(project_id: int, report: dict) -> asyncio.Task | None:
    """Из воркера: чистый отчёт молчит, находки — в фоне, конвейер не ждёт и не падает."""
    if not isinstance(report, dict) or report.get("ok"):
        return None
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return None
    task = loop.create_task(_guarded(int(project_id), report))
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
    return task


async def _guarded(project_id: int, report: dict) -> None:
    try:
        await handle_report(project_id, report)
    except Exception:  # noqa: BLE001
        logger.exception("content_checks: report hook #{} crashed", project_id)


async def handle_report(project_id: int, report: dict, *, app: Any = None) -> dict:
    """Пока идёт наше исправление проекта, его же повторный отчёт (после нашего повтора
    шага) не разбираем второй раз: итог скажет повторная проверка."""
    key = (oa._tenant_key(), int(project_id))
    if key in _BUSY:
        return {"status": "busy"}
    _BUSY.add(key)
    try:
        return await _handle(int(project_id), report, app)
    finally:
        _BUSY.discard(key)


def _busy_reason(project: Any) -> str:
    if project is None:
        return "проект не найден"
    try:
        from app.services.project_state import is_running_status

        if is_running_status(project.status):
            return "в проекте идёт шаг, правки подождут"
    except Exception:  # noqa: BLE001
        return ""
    return ""


def _run(app: Any):
    from app.services import orchestrator_autonomy_run as run

    return run, run._app(app)


async def _handle(project_id: int, report: dict, app: Any) -> dict:
    run, app = _run(app)
    cfg = oa.load()
    operator = cfg.get("updated_by") or ""
    step = str(report.get("step") or "")
    project = await run._project(project_id)
    blocked = _busy_reason(project)
    options = cc.fix_options(report, project_id)
    rerun = next((o for o in options if o.get("action") == "step.run_step"), None)
    run_plan, retry_cost = None, None
    if rerun is not None and cfg["auto_fix_on_failure"] and cfg["auto_retry"] and not blocked and operator:
        params = run.retry_params({"fix_options": [rerun]}, cfg["auto_spend_limit_usd"])
        run_plan, retry_cost = await run._priced_retry(app, params, operator, {"node_key": step})
    if rerun is not None:
        card = run.retry_params({"fix_options": [rerun]}, None)
        options = [dict(o, params=card) if o is rerun else o for o in options]
    decision = oa.decide_fix(
        cause={"code": "content_check"},
        fix_options=options,
        cfg=cfg,
        retries_done=oa.retries_done(project_id, retry_key(step)),
        retry_cost_usd=retry_cost,
        blocked_reason=blocked,
    )
    first = (report.get("findings") or [{}])[0]
    title = f"Проверка: {cc.STEP_RU.get(step, step)} — {first.get('title_ru') or 'есть расхождения'}"
    diag = {"cause": {"explain_ru": cc.summary_text(report)}, "errors": []}
    status = await run._notify(
        project_id,
        "warning",
        title,
        run.diagnosis_text(diag, decision),
        kind=KIND,
        key=f"{KIND}:{project_id}:{step}:{report.get('digest')}",
        actions=run._card_actions(decision),
    )
    out: dict[str, Any] = {"status": "reported" if status == "written" else status, "notice": status}
    out["auto"] = [s.action for s in decision.auto_steps]
    if status != "written" or not decision.auto_steps:
        return out
    done, failed = await _fix(app, project_id, step, report, decision, run_plan, operator, project)
    fresh = await recheck(project_id, step)
    if fresh is not None and fresh.get("ok"):
        oa.set_retries(project_id, retry_key(step), 0)
    await _result(project_id, step, report, done, failed, fresh, decision)
    out["status"] = "fixed" if fresh is not None and fresh.get("ok") else "not_fixed"
    out["fresh"] = fresh
    return out


async def _fix(
    app: Any,
    project_id: int,
    step: str,
    report: dict,
    decision: oa.FixDecision,
    run_plan: str | None,
    operator: str,
    project: Any,
) -> tuple[list[str], list[str]]:
    run, _ = _run(app)
    done: list[str] = []
    failed: list[str] = []
    # Повтор шага пересоздаёт кадры: точечные правки по старым uuid после него бессмысленны.
    rerun = run_plan is not None and any(s.action == "step.run_step" for s in decision.auto_steps)
    for item in decision.auto_steps:
        if rerun and item.action in oa.DATA_FIX_ACTIONS:
            continue
        if item.action == "step.run_step":
            if not run_plan:
                continue
            res = await _retry(app, project_id, step, report, run_plan, operator, project)
        else:
            res = await run.run_action(
                app, item.action, item.params, operator, f"проверка содержания: {item.title_ru}"
            )
        if res.get("ok"):
            done.append(item.title_ru)
        else:
            failed.append(f"{item.title_ru}: {res.get('error') or 'не вышло'}")
    return done, failed


async def _retry(
    app: Any, project_id: int, step: str, report: dict, plan_id: str, operator: str, project: Any
) -> dict:
    run, _ = _run(app)
    data_dir = getattr(project, "data_dir", None)
    if data_dir is not None:
        # Модель увидит, что именно не сошлось: тот же раздел, что у повтора контракта.
        oa.write_feedback(data_dir, step, cc.feedback_text(report))
    key = retry_key(step)
    before = oa.retries_done(project_id, key)
    oa.set_retries(project_id, key, before + 1)
    try:
        res = await run._launch(app, plan_id, operator, approve=True)
    finally:
        if data_dir is not None:
            oa.clear_feedback(data_dir, step)
    if not res.get("started"):
        oa.set_retries(project_id, key, before)
    return res


async def recheck(project_id: int, step: str) -> dict | None:
    """Проверка заново по свежим данным; отчёт пишется в meta, если проект не в работе."""
    from app.db import session_scope
    from app.models import Project

    try:
        async with session_scope() as s:
            project = await s.get(Project, int(project_id))
            if project is None:
                return None
            fresh = await cc.run_checks(s, project, step)
            if not _busy_reason(project):
                cc.save_report(project, fresh)
                await s.commit()
            return fresh
    except Exception as exc:  # noqa: BLE001
        logger.warning("content_checks: recheck #{} {} failed: {}", project_id, step, exc)
        return None


async def _result(
    project_id: int,
    step: str,
    report: dict,
    done: list[str],
    failed: list[str],
    fresh: dict | None,
    decision: oa.FixDecision,
) -> str:
    run, _ = _run(None)
    step_ru = cc.STEP_RU.get(step, step)
    parts: list[str] = []
    if done:
        parts.append("Исправил сам: " + "; ".join(done) + ".")
    if failed:
        parts.append("Не получилось: " + "; ".join(failed) + ".")
    if fresh is None:
        parts.append("Повторно проверить не смог.")
        level = "warning"
    elif fresh.get("ok"):
        parts.append("Повторная проверка: всё сходится.")
        level = "success"
    else:
        parts.append("Повторная проверка: " + cc.summary_text(fresh))
        level = "warning"
    actions = run._card_actions(decision) if fresh is not None and not fresh.get("ok") else []
    return await run._notify(
        project_id,
        level,
        f"Проверка: {step_ru} — " + ("исправлено" if level == "success" else "осталось исправить"),
        " ".join(parts),
        kind=KIND_FIX,
        key=f"{KIND_FIX}:{project_id}:{step}:{report.get('digest')}",
        actions=actions,
    )
