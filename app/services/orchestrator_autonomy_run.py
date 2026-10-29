"""Сбой ноды → разбор в чат → исправление сам или карточка (``orchestrator_autonomy``).

Вызов — из воркера после ``record_step_failure`` с итогом, где нода реально стала failed
(сон после серии повторов, остановка, пауза из-за денег). Внутренние повторы контракта и
мягкие повторы воркера сюда не доходят. Разбор — ``diag.node_failure`` (без LLM), пишется
уведомлением чата (``chat_notice``): оно хранится в базе и приходит в чат проекта, даже если
чат сейчас не открыт. Ключ уведомления — сам сбой (NodeRun и время падения), поэтому один и
тот же сбой даёт одно сообщение и одно исправление.

Исполнение — через слой действий, как у карточек чата: план, проверка предусловий и цены,
стоп-кран, занятость проекта, журнал. Платный перезапуск подтверждается от имени человека,
который включил автономный режим (его личность записана в настройке), с потолком
``auto_spend_limit_usd``. Без карточки идут только ``node.set_model`` (не дороже) и
``step.run_step`` — по решению ``decide_fix``. Потом проверяем по NodeRun, что нода готова, и
пишем итог. Не помогла новая модель — возвращаем прежнюю (``node.revert_model``).

Ошибки здесь не доходят до конвейера: всё в фоне и под try.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
from typing import Any

from loguru import logger

from app.services import orchestrator_autonomy as oa

# Итоги record_step_failure, после которых нода в статусе failed.
FAILURE_ACTIONS = frozenset({"sleep", "abandon", "pause_infra", "pause_budget"})
# Повтор упрётся в то же самое и сожжёт деньги — только разбор и карточка.
BLOCKED_RU = {
    "pause_budget": "исчерпан бюджет прогона проекта",
    "pause_infra": "кончились деньги у провайдера или не поднялась инфраструктура",
}
KIND_FAILURE = "node_failure"
KIND_RESULT = "node_failure_fix"
REPEAT_NOTICE_S = 24 * 3600
MAX_CARD_ACTIONS = 4

_BUSY: set[tuple[str, int]] = set()
_TASKS: set[asyncio.Task] = set()


def on_step_failure(project_id: int, step_action: str) -> asyncio.Task | None:
    """Из воркера: разбор и исправление в фоне, конвейер не ждёт и не падает."""
    if step_action not in FAILURE_ACTIONS:
        return None
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return None
    task = loop.create_task(_guarded(int(project_id), step_action))
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
    return task


async def _guarded(project_id: int, step_action: str) -> None:
    try:
        report = await handle_failure(project_id, step_action)
        logger.info("orchestrator_autonomy: failure #{} ({}) → {}", project_id, step_action, report)
    except Exception:  # noqa: BLE001
        logger.exception("orchestrator_autonomy: failure hook #{} crashed", project_id)


async def handle_failure(project_id: int, step_action: str, *, app: Any = None) -> dict:
    """Один сбой проекта. Пока идёт наше исправление, новые сбои того же проекта — его же
    повторы: о них скажет сам цикл исправления, второго разбора не пишем."""
    key = (oa._tenant_key(), int(project_id))
    if key in _BUSY:
        return {"status": "busy"}
    _BUSY.add(key)
    try:
        return await _incident(int(project_id), step_action, app)
    finally:
        _BUSY.discard(key)


# ---------- чтение ----------


def _app(app: Any) -> Any:
    if app is not None:
        return app
    from app.orchestrator import app_ref

    return app_ref.current()


async def diagnose(app: Any, project_id: int, operator: str) -> dict | None:
    from app.services.action_layer import node_failure as nf
    from app.services.action_layer.context import ActionContext

    try:
        ctx = ActionContext(app=_app(app), operator=operator or "system", read_only=True)
        return await nf._diag_node_failure(ctx, {"project_id": int(project_id)})
    except Exception as exc:  # noqa: BLE001
        logger.warning("orchestrator_autonomy: diag #{} failed: {}: {}", project_id, type(exc).__name__, exc)
    # Разбор недоступен (нет приложения, нет личности для внутреннего вызова): сообщение о
    # сбое всё равно нужно — по ошибке из NodeRun, без исправлений.
    return await _bare_diag(project_id)


async def _bare_diag(project_id: int) -> dict | None:
    from sqlalchemy import select

    from app.db import session_scope
    from app.models import NodeRun, NodeRunStatus, WorkflowRun
    from app.services.action_layer.node_failure import CAUSE_RU

    async with session_scope() as s:
        nr = await s.scalar(
            select(NodeRun)
            .join(WorkflowRun, WorkflowRun.id == NodeRun.workflow_run_id)
            .where(WorkflowRun.project_id == int(project_id), NodeRun.status == NodeRunStatus.failed)
            .order_by(NodeRun.finished_at.desc(), NodeRun.id.desc())
        )
    if nr is None:
        return None
    return {
        "found": True,
        "node_key": nr.node_key,
        "title": nr.node_key,
        "errors": [str(nr.error)[:1500]] if nr.error else [],
        "cause": {"code": "unknown", "title_ru": CAUSE_RU["unknown"], "explain_ru": ""},
        "fix_options": [],
    }


async def failed_run(project_id: int, node_key: str) -> dict | None:
    """Запись падения ноды: id NodeRun и время. None — нода сейчас не в failed."""
    from sqlalchemy import select

    from app.db import session_scope
    from app.models import NodeRun, NodeRunStatus, WorkflowRun

    async with session_scope() as s:
        nr = await s.scalar(
            select(NodeRun)
            .join(WorkflowRun, WorkflowRun.id == NodeRun.workflow_run_id)
            .where(WorkflowRun.project_id == int(project_id), NodeRun.node_key == node_key)
        )
        if nr is None or nr.status != NodeRunStatus.failed:
            return None
        when = nr.finished_at.isoformat() if nr.finished_at else f"a{int(nr.attempts or 0)}"
        return {"id": int(nr.id), "failure_id": f"{nr.id}:{when}", "error": nr.error or ""}


async def node_done(project_id: int, node_key: str) -> bool:
    from sqlalchemy import select

    from app.db import session_scope
    from app.models import NodeRun, NodeRunStatus, WorkflowRun

    async with session_scope() as s:
        status = await s.scalar(
            select(NodeRun.status)
            .join(WorkflowRun, WorkflowRun.id == NodeRun.workflow_run_id)
            .where(WorkflowRun.project_id == int(project_id), NodeRun.node_key == node_key)
        )
    return status == NodeRunStatus.done


async def _project(project_id: int):
    from app.db import session_scope
    from app.models import Project

    async with session_scope() as s:
        return await s.get(Project, int(project_id))


def model_candidate(meta: dict | None, node_key: str, tried: set[str]) -> dict:
    """Текущая модель ноды и лучшая замена: сначала той же цены, потом дешевле, потом прочие.

    Пусто, если у ноды нет выбора модели (или модель зафиксирована группой «Кадры»).
    """
    from app.services import node_edit as ne

    try:
        state = ne.model_state(meta, node_key)
        if not state.get("kind") or state.get("forced_vibecode"):
            return {}
        opts = ne.model_options(meta, node_key).get("options") or []
    except Exception:  # noqa: BLE001
        return {}
    cur_id = state.get("model_id")
    rank = {"same": 0, "cheaper": 1, "costlier": 2, "unknown": 3}
    pool = [
        o
        for o in opts
        if o.get("id") and o["id"] != cur_id and o["id"] not in tried and o.get("cost_vs_current") in rank
    ]
    if not pool:
        return {"current_model_id": cur_id, "current_price": ne.price_points(ne.find_catalog_model(cur_id))}
    best = min(pool, key=lambda o: rank[o["cost_vs_current"]])
    return {
        "current_model_id": cur_id,
        "current_price": ne.price_points(ne.find_catalog_model(cur_id)),
        "new_model_id": best["id"],
        "new_label": best.get("label") or best["id"],
        "new_price": ne.price_points(ne.find_catalog_model(best["id"])),
    }


def retry_params(diag: dict, limit_usd: float | None) -> dict | None:
    """Параметры перезапуска из разбора + потолок расхода и остановка на этой же ноде.

    ``limit_usd`` — потолок автозапуска; None — карточка человеку с обычным потолком чата.
    """
    from app.services.action_layer.builtin_launch import with_default_ceiling
    from app.services.kie_catalog import CREDIT_USD

    # Разбивка перед пустым входом (upstream) — другой шаг, а не перезапуск упавшей ноды.
    opt = next(
        (f for f in diag.get("fix_options") or [] if f.get("action") == "step.run_step" and not f.get("upstream")),
        None,
    )
    if opt is None:
        return None
    params = dict(opt.get("params") or {})
    if limit_usd is None:
        params = with_default_ceiling(params)
    else:
        # Потолок слоя действий в кредитах: столько резервируется на этот запуск.
        params["ceiling_credits"] = max(0.01, round(float(limit_usd) / float(CREDIT_USD), 2))
    if params.get("step_code") == "excel_gpt" and params.get("node_key"):
        # Иначе Студия после ноды сама пойдёт по стрелкам дальше и потратит больше.
        params["stop_after_node_key"] = params["node_key"]
    return params


def _with_run_params(opt: dict, run_card: dict | None) -> dict:
    if opt.get("action") != "step.run_step":
        return opt
    if opt.get("upstream"):
        from app.services.action_layer.builtin_launch import with_default_ceiling

        return dict(opt, params=with_default_ceiling(dict(opt.get("params") or {})))
    return dict(opt, params=run_card) if run_card is not None else opt


# ---------- слой действий ----------


async def _plan(app: Any, action: str, params: dict, operator: str, note: str) -> tuple[str | None, dict]:
    from app.services.action_layer import approval, service

    return await service.create_plan(
        app,
        [{"action_id": action, "params": dict(params)}],
        operator=approval.OperatorIdentity(operator),
        request_text=f"Автономный режим оркестратора: {note}"[:2000],
    )


async def _launch(app: Any, plan_id: str, operator: str, *, approve: bool) -> dict:
    """Запуск плана. ``approve`` — подтверждение от имени человека, включившего режим."""
    from app.services.action_layer import approval, executor, service

    who = approval.OperatorIdentity(operator)
    try:
        if approve:
            await service.approve_plan(app, plan_id, who)
        await service.prepare_run(plan_id, who)
    except (service.RunRefused, approval.ApprovalError, LookupError) as exc:
        return {"ok": False, "started": False, "error": str(exc)}
    task = executor.launch(app, plan_id, who.id)
    try:
        await asyncio.shield(task)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "started": True, "error": f"{type(exc).__name__}: {exc}"}
    view = await service.plan_view(plan_id)
    step = (view.get("steps") or [{}])[0]
    ok = step.get("status") == "succeeded"
    result = step.get("result") if isinstance(step.get("result"), dict) else {}
    return {
        "ok": ok,
        "started": True,
        "error": "" if ok else str(step.get("error") or view.get("error") or step.get("status") or ""),
        "text_ru": str(result.get("text_ru") or ""),
        "credits": (step.get("cost") or {}).get("actual"),
    }


async def run_action(app: Any, action: str, params: dict, operator: str, note: str) -> dict:
    """Бесплатное действие без подтверждения (смена модели не дороже, возврат модели)."""
    try:
        plan_id, preview = await _plan(app, action, params, operator, note)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "started": False, "error": f"{type(exc).__name__}: {exc}"}
    if plan_id is None:
        return {
            "ok": False,
            "started": False,
            "error": "; ".join(map(str, preview.get("errors") or []))[:400],
        }
    return await _launch(app, plan_id, operator, approve=bool(preview.get("needs_approval")))


def _usd(credits: Any) -> float | None:
    from app.services.kie_catalog import CREDIT_USD

    if credits is None or isinstance(credits, bool):
        return None
    try:
        return round(float(credits) * float(CREDIT_USD), 4)
    except (TypeError, ValueError):
        return None


# ---------- чат ----------


def _card_actions(decision: oa.FixDecision, extra: list[dict] | None = None) -> list[dict]:
    out = [
        {"action_id": s.action, "label": s.title_ru[:80], "params": dict(s.params)}
        for s in decision.card_steps
    ]
    return [*(extra or []), *out][:MAX_CARD_ACTIONS]


def diagnosis_text(diag: dict, decision: oa.FixDecision) -> str:
    cause = diag.get("cause") or {}
    parts = [str(cause.get("explain_ru") or "").strip()]
    if diag.get("model"):
        parts.append(f"Модель: {diag['model']}.")
    errors = [str(e) for e in diag.get("errors") or [] if e]
    # Причина уже названа словами — сырая строка ошибки с ключами нод («#30 n_excel_gpt_1: …») лишняя.
    if errors and (cause.get("code") in (None, "", "unknown") or not parts[0]):
        parts.append("Ошибка: " + _sentence(_NODE_PREFIX.sub("", errors[0])[:400]))
    parts += [_sentence(n) for n in decision.notes if n]
    if decision.auto_steps:
        parts.append("Чиню сам: " + "; ".join(s.title_ru for s in decision.auto_steps) + ".")
    checks = [s for s in decision.card_steps if s.action in oa._READ_ONLY_FIXES]
    cards = [s for s in decision.card_steps if s.action not in oa._READ_ONLY_FIXES]
    if cards:
        head = "Можно ещё (с подтверждением)" if decision.auto_steps else "Предлагаю (нужно подтверждение)"
        parts.append(
            head
            + ": "
            + "; ".join(s.title_ru + (f" — сам не запускаю: {s.why_ru}" if s.why_ru else "") for s in cards)
            + "."
        )
    if checks:
        parts.append("Для проверки есть кнопка: " + "; ".join(f"«{s.title_ru}»" for s in checks) + ".")
    return " ".join(_sentence(p) for p in parts if p)


_NODE_PREFIX = re.compile(r"^\s*#\d+\s+n_[a-z0-9_]+:\s*")


def _sentence(text: str) -> str:
    text = str(text or "").strip()
    return text if not text or text[-1] in ".!?…»" else text + "."


async def _notify(
    project_id: int, level: str, title: str, text: str, *, kind: str, key: str, actions=None, window_s=None
) -> str:
    from app.services import chat_notice

    project = await _project(project_id)
    with chat_notice.owner_scope(getattr(project, "tenant_id", None)):
        status, _ = await chat_notice.send(
            project_id,
            level,
            title,
            text,
            kind=kind,
            dedup_key=key,
            dedup_window_s=chat_notice.FOREVER_S if window_s is None else window_s,
            suggested_actions=actions or [],
        )
    return status


# ---------- цикл ----------


async def _incident(project_id: int, step_action: str, app: Any) -> dict:
    app = _app(app)
    cfg = oa.load()
    operator = cfg.get("updated_by") or ""
    blocked = BLOCKED_RU.get(step_action, "")
    tried: set[str] = set()
    report: dict[str, Any] = {"status": "noop", "rounds": []}
    for _round in range(int(cfg["max_auto_retries_per_node"]) + 1):
        diag = await diagnose(app, project_id, operator)
        if not diag or not diag.get("found") or not diag.get("node_key"):
            return report
        node_key = str(diag["node_key"])
        failed = await failed_run(project_id, node_key)
        if failed is None:
            return report
        project = await _project(project_id)
        if _round and _stopped(project_id, project):
            # «Стоп» человека посреди серии автоповторов: дальше сами не идём.
            report["status"] = "stopped"
            return report
        meta = project.meta if project is not None and isinstance(project.meta, dict) else {}
        model = model_candidate(meta, node_key, tried)
        run_card = retry_params(diag, None)
        run_plan, retry_cost = None, None
        if (
            run_card is not None
            and cfg["auto_fix_on_failure"]
            and cfg["auto_retry"]
            and not blocked
            and operator
        ):
            run_auto = retry_params(diag, cfg["auto_spend_limit_usd"])
            run_plan, retry_cost = await _priced_retry(app, run_auto, operator, diag)
        options = [_with_run_params(f, run_card) for f in diag.get("fix_options") or []]
        decision = oa.decide_fix(
            cause=diag.get("cause") or {},
            fix_options=options,
            cfg=cfg,
            retries_done=oa.retries_done(project_id, node_key),
            current_price=model.get("current_price"),
            new_price=model.get("new_price"),
            new_model_id=model.get("new_model_id"),
            retry_cost_usd=retry_cost,
            blocked_reason=blocked,
        )
        why = (diag.get("cause") or {}).get("title_ru") or "сбой"
        title = f"Нода «{diag.get('title') or node_key}» упала: {why}"
        fid = failed["failure_id"]
        text = diagnosis_text(diag, decision)
        key, window = f"{KIND_FAILURE}:{project_id}:{fid}", None
        if not decision.auto_steps:
            # Шаг сам повторяет ноду по своей политике (раз в ~30 мин): та же причина и тот же
            # совет — одно уведомление в сутки, а не по штуке на попытку.
            digest = hashlib.sha1("|".join((node_key, title, text)).encode()).hexdigest()[:16]
            key, window = f"{KIND_FAILURE}:{project_id}:same:{digest}", REPEAT_NOTICE_S
        status = await _notify(
            project_id,
            "error",
            title,
            text,
            kind=KIND_FAILURE,
            key=key,
            actions=_card_actions(decision),
            window_s=window,
        )
        report["rounds"].append(
            {"failure_id": fid, "notice": status, "auto": [s.action for s in decision.auto_steps]}
        )
        if status != "written" or not decision.auto_steps:
            report["status"] = "reported" if status == "written" else status
            return report
        outcome = await _fix(app, project_id, node_key, diag, decision, run_plan, operator, model, fid)
        report["status"] = outcome["status"]
        if outcome["status"] != "retry_failed":
            return report
        if model.get("new_model_id"):
            tried.add(str(model["new_model_id"]))
    return report


def _stopped(project_id: int, project: Any) -> bool:
    from app.services.gen_queue_run import is_user_stopped
    from app.services.step_cancel import is_stop_requested

    return is_stop_requested(int(project_id)) or (project is not None and is_user_stopped(project))


async def _priced_retry(app: Any, run: dict, operator: str, diag: dict) -> tuple[str | None, float | None]:
    """План перезапуска и его верхняя оценка в $; предупреждение или ошибка проверки — без цены."""
    try:
        plan_id, preview = await _plan(
            app, "step.run_step", run, operator, f"перезапуск ноды {diag.get('node_key')}"
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("orchestrator_autonomy: retry plan failed: {}", exc)
        return None, None
    # Предупреждения запуска (сброс готовых нод ниже, идущий шаг, «Стоп» человека,
    # автопродвижение) — решение человека: без цены правило отдаст карточку.
    if plan_id is None or preview.get("warnings"):
        return None, None
    cost = preview.get("cost") or {}
    if not cost.get("known"):
        return plan_id, None
    return plan_id, _usd(cost.get("high"))


def _feedback_text(diag: dict) -> str:
    cause = diag.get("cause") or {}
    lines = [f"Причина: {cause.get('title_ru') or ''}. {cause.get('explain_ru') or ''}".strip()]
    lines += [f"Ошибка: {str(e)[:800]}" for e in (diag.get("errors") or [])[:2] if e]
    for r in (diag.get("rejects") or [])[-2:]:
        if r.get("error"):
            lines.append(f"Отклонено ({r.get('kind') or 'формат'}): {str(r['error'])[:600]}")
    return "\n".join(lines)


async def _fix(
    app: Any,
    project_id: int,
    node_key: str,
    diag: dict,
    decision: oa.FixDecision,
    run_plan: str | None,
    operator: str,
    model: dict,
    fid: str,
) -> dict:
    label = diag.get("title") or node_key
    done: list[str] = []
    switched = False
    retried = None
    run_card: list[dict] = []
    for step in decision.auto_steps:
        if step.action == "node.set_model":
            res = await run_action(app, step.action, step.params, operator, f"модель ноды {node_key}")
            if not res["ok"]:
                text = f"Нода «{label}»: не смог поставить модель: {res['error']}."
                await _result(project_id, fid, "error", text, decision)
                return {"status": "model_failed"}
            switched = True
            done.append(f"поставил модель «{model.get('new_label') or step.params.get('model_id')}»")
        elif step.action == "step.run_step" and run_plan:
            run_card = [{"action_id": step.action, "label": step.title_ru[:80], "params": dict(step.params)}]
            retried = await _retry(app, project_id, node_key, diag, run_plan, operator)
            done.append("перезапустил ноду")
    head = f"Нода «{label}»: " + ", ".join(done) if done else f"Нода «{label}»"
    if retried is None:
        extra = []
        if switched:
            extra.append(
                {
                    "action_id": "node.revert_model",
                    "label": "Вернуть прежнюю модель",
                    "params": {"project_id": project_id, "node_key": node_key},
                }
            )
        text = head + ". Перезапуск — по кнопке, без подтверждения не запускаю."
        await _result(project_id, fid, "info", text, decision, extra)
        return {"status": "switched_only" if switched else "nothing"}
    if retried["ok"]:
        oa.set_retries(project_id, node_key, 0)
        spent = _usd(retried.get("credits"))
        text = head + " — теперь готово." + (f" Потрачено ${spent:.2f}." if spent else "")
        await _result(project_id, fid, "success", text, None)
        return {"status": "fixed"}
    started = bool(retried.get("started"))
    text = head + f" — не помогло: {retried['error'] or 'нода не готова'}."
    if not started:
        text = f"Нода «{label}»: сам запустить не смог: {retried['error']}. Перезапуск — по кнопке."
    if switched:
        back = await run_action(
            app,
            "node.revert_model",
            {"project_id": project_id, "node_key": node_key},
            operator,
            f"возврат модели ноды {node_key}",
        )
        text += (
            " Вернул прежнюю модель." if back["ok"] else f" Прежнюю модель вернуть не смог: {back['error']}."
        )
    # Не запустился (бюджет, стоп-кран, проект занят) — повтор остаётся человеку кнопкой.
    await _result(project_id, fid, "warning", text, None, run_card if not started else None)
    return {"status": "retry_failed" if started else "not_started"}


async def _retry(app: Any, project_id: int, node_key: str, diag: dict, plan_id: str, operator: str) -> dict:
    project = await _project(project_id)
    data_dir = getattr(project, "data_dir", None) if project is not None else None
    # «Работа с GPT» читает файл по ключу ноды, план/закадр/разбивка/промты — по коду
    # шага (retry_feedback): пишем под обоими, иначе повтор разбивки шёл бы вслепую.
    step = str(diag.get("step") or "")
    keys = [node_key, *([step] if step and step not in ("excel_gpt", node_key) else [])]
    if data_dir is not None:
        # Модель увидит, почему прошлый ответ отклонён: тот же раздел, что у повтора контракта.
        for key in keys:
            oa.write_feedback(data_dir, key, _feedback_text(diag))
    # Счёт до запуска: упавший посреди прогона сервер не даёт лишнего бесплатного повтора.
    before = oa.retries_done(project_id, node_key)
    oa.set_retries(project_id, node_key, before + 1)
    try:
        res = await _launch(app, plan_id, operator, approve=True)
    finally:
        if data_dir is not None:
            for key in keys:
                oa.clear_feedback(data_dir, key)
    if not res.get("started"):
        oa.set_retries(project_id, node_key, before)
    if res["ok"] and not await node_done(project_id, node_key):
        res = {**res, "ok": False, "error": "шаг завершился, но нода не в статусе «готово»"}
    return res


async def _result(project_id: int, fid: str, level: str, text: str, decision, extra=None) -> str:
    actions = _card_actions(decision, extra) if decision is not None else (extra or [])
    title = text.split(" — ")[0].split(". ")[0][:200]
    return await _notify(
        project_id,
        level,
        title,
        text,
        kind=KIND_RESULT,
        key=f"{KIND_RESULT}:{project_id}:{fid}",
        actions=actions,
    )
