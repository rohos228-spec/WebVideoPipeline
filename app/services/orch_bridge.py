from __future__ import annotations

import asyncio
import contextlib
import json
import re
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

from loguru import logger
from sqlalchemy import select, update

from app.services.action_layer import approval, executor, service, store
from app.services.action_layer.card_text import card_ru
from app.services.action_layer.spec import ensure_builtin_actions, registry

CARD_EVENT = "orch_card_approved"
REVOKE_EVENT = "orch_card_revoked"
PROGRESS_EVENT = "step_progress"
RESULT_EVENT = "message"
NOTICE_EVENT = "chat_notice"
NOTICE_BACKLOG = 10
NOTICE_SWEEP_S = 15.0
PROGRESS_S = 1.0
# Сам шаг бесплатный, но запускает платную работу дальше (очередь, автопродвижение).
LATER_SPEND_TEXT = (
    "неизвестна: сам шаг бесплатный, но дальше воркер запустит платные шаги, их сумму заранее не посчитать"
)

_CHANGE_ID = re.compile(r"^orch-appr-(\d+)-(\d+)$")
_OWN_EXECUTORS = frozenset(
    {
        "montage.delete_frame",
        "montage.delete_child_frame",
        "frame.restore",
        "group.delete",
        "prompt.delete_master",
    }
)
_NO_RUN_REASONS = frozenset({"paid_outcome_unknown", "paid_unknown_outcome", "partial_write"})
#: Карточка → одобренный план. Ключ несёт арендатора: память процесса общая для всех
#: клиентов студии, и id карточки другого клиента не должен находить (и снимать) чужую привязку.
_BOUND: dict[tuple[str | None, str], str] = {}


def _bound_key(change_id: str) -> tuple[str | None, str]:
    from app.services.tenant import current_tenant

    return current_tenant(), str(change_id)


_RELAYS: dict[tuple[int, str, int], dict] = {}
_SEEN: dict[str, set[int]] = {}
_SEEN_LOCKS: dict[str, asyncio.Lock] = {}


class DecisionError(Exception):
    def __init__(self, status: int, detail: Any):
        self.status = status
        self.detail = detail
        super().__init__(str(detail))


def credit_usd() -> float:
    from app.services.kie_catalog import CREDIT_USD

    return float(CREDIT_USD)


def to_usd(credits: Any) -> float | None:
    if credits is None or isinstance(credits, bool):
        return None
    try:
        return round(float(credits) * credit_usd(), 4)
    except (TypeError, ValueError):
        return None


def _spec(name: Any):
    ensure_builtin_actions()
    return registry.find(str(name or ""))


def bridged(name: Any) -> bool:
    spec = _spec(name)
    return spec is not None and spec.risk != "read" and spec.id not in _OWN_EXECUTORS


def plan_required(name: Any) -> bool:
    spec = _spec(name)
    return bridged(name) and bool(spec.needs_approval)


def _creator(task_id: Any) -> approval.OperatorIdentity:
    return approval.OperatorIdentity(f"orchestrator:task-{task_id}" if task_id else "orchestrator")


def _steps(name: str, params: dict) -> list[dict]:
    return [{"action_id": str(name), "params": dict(params or {})}]


def _request_text(spec, goal: Any = "") -> str:
    head = f"Чат оркестратора: {spec.title_ru}"
    text = str(goal or "").strip()
    return (f"{head}. Задача: {text}" if text else head)[:2000]


def _effect_texts(preview: dict, labels: dict[str, str] | None = None) -> list[str]:
    out = []
    for item in preview.get("destructive_effects") or []:
        text = str(item.get("text") or "") if isinstance(item, dict) else str(item)
        if text and not text.startswith("Внимание: "):
            out.append(card_ru(text, labels))
    return out


def card_from_preview(preview: dict, labels: dict[str, str] | None = None) -> dict:
    cost = preview.get("cost") or {}
    known = bool(cost.get("known"))
    low, high = cost.get("low"), cost.get("high")
    card: dict[str, Any] = {
        "cost_estimate": to_usd(high) if known else None,
        "cost": {
            "known": known,
            "low_usd": to_usd(low) if known else None,
            "high_usd": to_usd(high) if known else None,
            "credits_low": low if known else None,
            "credits_high": high if known else None,
            "unknown_steps": int(cost.get("unknown_steps") or 0),
        },
        "warnings": [
            card_ru(w.get("text") or "", labels) for w in preview.get("warnings") or [] if isinstance(w, dict)
        ],
        "destructive_effects": _effect_texts(preview, labels),
        "future_spend": [
            card_ru(f.get("text") or "", labels)
            for f in preview.get("future_spend_effects") or []
            if isinstance(f, dict)
        ],
    }
    if known and not high and card["future_spend"]:
        # «около $0» рядом с «воркер потратит деньги» сбивает: цены нет, траты будут.
        card["cost_estimate"] = None
        card["cost_text"] = LATER_SPEND_TEXT
    if not preview.get("ok"):
        card["preview_errors"] = [card_ru(e, labels) for e in (preview.get("errors") or [])][:10]
        card["blocked"] = True
    return card


async def prepare_card(name: str, params: dict | None, ctx: dict | None) -> dict:
    card = await _prepare_card(name, params, ctx)
    if card and name == "history.rollback":
        # Номер снимка человеку ничего не говорит: что именно вернётся и что откат обратим.
        from app.services.action_layer.chat_undo import rollback_card

        with contextlib.suppress(Exception):
            card.update(await rollback_card(dict(params or {})))
    if card and name in _CC_CARDS:
        # Номер предложения и контрольная сумма человеку ничего не говорят: какая нода, какой
        # проект, что добавится или уберётся и можно ли вернуть.
        from app.services.action_layer import chat_proposals

        with contextlib.suppress(Exception):
            card.update(await getattr(chat_proposals, _CC_CARDS[name])(dict(params or {})))
    return card


_CC_CARDS = {
    "change.apply": "apply_card",
    "change.rollback": "rollback_card",
    "change.reject": "reject_card",
}


async def _prepare_card(name: str, params: dict | None, ctx: dict | None) -> dict:
    spec = _spec(name)
    if spec is None:
        return {}
    ctx = ctx if isinstance(ctx, dict) else {}
    labels = ctx.get("labels") if isinstance(ctx.get("labels"), dict) else None
    card: dict[str, Any] = {"title": spec.title_ru, "risk_tier": spec.risk}
    if spec.destructive_effects is not None:
        with contextlib.suppress(Exception):
            card["destructive_effects"] = [card_ru(spec.destructive_effects(dict(params or {})), labels)]
    if not plan_required(spec.id):
        return card
    try:
        plan_id, preview = await service.create_plan(
            ctx.get("app"),
            _steps(spec.id, params or {}),
            operator=_creator(ctx.get("task_id")),
            request_text=_request_text(spec, ctx.get("goal")),
        )
    except Exception as exc:
        logger.warning("orch bridge: card preview failed for {}: {}", spec.id, type(exc).__name__)
        card["preview_errors"] = ["не удалось посчитать оценку и проверить условия шага"]
        card["blocked"] = True
        # Шаг не проверен, а не запрещён: супервизор не выдаёт такую карточку за отказ условий.
        card["preview_failed"] = True
        return card
    card.update(card_from_preview(preview, labels))
    if plan_id:
        card["plan_id"] = plan_id
    return card


async def _plan_steps(plan_id: str) -> tuple[Any, list]:
    async with store.sessions()() as s:
        row = await s.get(store.ActionPlan, plan_id)
        steps = list(
            (
                await s.scalars(
                    select(store.ActionPlanStep)
                    .where(store.ActionPlanStep.plan_id == plan_id)
                    .order_by(store.ActionPlanStep.idx)
                )
            ).all()
        )
    return row, steps


async def plan_matches(plan_id: str, name: str, params: dict) -> bool:
    row, steps = await _plan_steps(plan_id)
    if row is None or len(steps) != 1:
        return False
    return steps[0].action_id == name and dict(steps[0].params or {}) == dict(params or {})


async def _reusable(plan_id: str | None, name: str, params: dict) -> bool:
    if not plan_id:
        return False
    row, _steps_rows = await _plan_steps(plan_id)
    return row is not None and row.status == store.PLAN_PROPOSED and await plan_matches(plan_id, name, params)


async def bind_approved(change_id: str, plan_id: str, *, action: str, task_id: Any, actor: str) -> None:
    _BOUND[_bound_key(change_id)] = plan_id
    async with store.sessions()() as s:
        store.append_history(
            s,
            event=CARD_EVENT,
            plan_id=plan_id,
            actor=actor,
            action_id=action,
            params={"change_id": change_id, "task_id": task_id},
            outcome="approved",
        )
        await s.commit()


async def bound_plan(change_id: str | None, action: str) -> str | None:
    if not change_id:
        return None
    if _bound_key(change_id) in _BOUND:
        return _BOUND[_bound_key(change_id)]
    async with store.sessions()() as s:
        rows = (
            await s.scalars(
                select(store.ActionHistory)
                .where(store.ActionHistory.event == CARD_EVENT, store.ActionHistory.action_id == action)
                .order_by(store.ActionHistory.id.desc())
                .limit(200)
            )
        ).all()
    for row in rows:
        if isinstance(row.params, dict) and row.params.get("change_id") == change_id:
            return row.plan_id
    return None


async def revoke(plan_id: str, change_id: str | None, actor: str) -> None:
    if change_id and _BOUND.get(_bound_key(change_id)) == plan_id:
        _BOUND.pop(_bound_key(change_id), None)
    now = datetime.utcnow()
    async with store.sessions()() as s:
        await s.execute(
            update(store.ActionApproval)
            .where(
                store.ActionApproval.plan_id == plan_id,
                store.ActionApproval.used_at.is_(None),
                store.ActionApproval.expires_at > now,
            )
            .values(expires_at=now)
        )
        store.append_history(
            s,
            event=REVOKE_EVENT,
            plan_id=plan_id,
            actor=actor,
            params={"change_id": change_id},
            outcome="revoked",
        )
        await s.commit()


async def approve_for_card(
    app: Any,
    *,
    change_id: str,
    task_id: Any,
    action: str,
    params: dict,
    card_plan_id: str | None,
    operator: approval.OperatorIdentity,
    goal: str = "",
) -> str:
    plan_id, _fresh = await _approve_card(
        app,
        change_id=change_id,
        task_id=task_id,
        action=action,
        params=params,
        card_plan_id=card_plan_id,
        operator=operator,
        goal=goal,
    )
    return plan_id


async def _live_approval(plan_id: str) -> bool:
    """У плана есть неиспользованное и не истёкшее подтверждение."""
    now = datetime.utcnow()
    async with store.sessions()() as s:
        found = await s.scalar(
            select(store.ActionApproval.id)
            .where(
                store.ActionApproval.plan_id == plan_id,
                store.ActionApproval.used_at.is_(None),
                store.ActionApproval.expires_at > now,
            )
            .limit(1)
        )
    return found is not None


async def _approve_card(
    app: Any,
    *,
    change_id: str,
    task_id: Any,
    action: str,
    params: dict,
    card_plan_id: str | None,
    operator: approval.OperatorIdentity,
    goal: str = "",
) -> tuple[str, bool]:
    """(plan_id, fresh). Повтор по уже одобренной карточке нового подтверждения не выпускает:
    create_approval гасит прежнее, и шаг, который его ждёт, падал бы без подтверждения."""
    spec = _spec(action)
    bound = await bound_plan(change_id, action)
    if bound and await plan_matches(bound, action, params) and await _live_approval(bound):
        return bound, False
    plan_id = card_plan_id if await _reusable(card_plan_id, action, params) else None
    if plan_id is None:
        plan_id, preview = await service.create_plan(
            app, _steps(action, params), operator=_creator(task_id), request_text=_request_text(spec, goal)
        )
        if plan_id is None:
            errors = "; ".join(str(e) for e in (preview.get("errors") or [])[:5])
            raise approval.ApprovalError(
                "plan_invalid", "Шаг не прошёл проверку: " + (errors or "условия не выполнены")
            )
    await service.approve_plan(app, plan_id, operator)
    await bind_approved(change_id, plan_id, action=action, task_id=task_id, actor=operator.id)
    return plan_id, True


async def _approver(plan_id: str) -> str | None:
    async with store.sessions()() as s:
        return await s.scalar(
            select(store.ActionApproval.operator)
            .where(store.ActionApproval.plan_id == plan_id)
            .order_by(store.ActionApproval.created_at.desc())
        )


async def _publish(ctx: dict, event_type: str, payload: dict) -> None:
    bus = ctx.get("bus")
    session_id = ctx.get("session_id")
    if bus is None or not session_id:
        return
    try:
        await bus.publish(str(session_id), event_type, payload, task_id=ctx.get("task_id"))
    except Exception as exc:
        logger.warning("orch bridge: publish {} failed: {}", event_type, type(exc).__name__)


async def _progress(plan_id: str) -> tuple | None:
    _row, steps = await _plan_steps(plan_id)
    if not steps:
        return None
    step = steps[0]
    return (step.status, step.progress, step.progress_note or "")


async def _follow(task: asyncio.Task, plan_id: str, spec, ctx: dict, operator: str) -> None:
    last = None
    try:
        while not task.done():
            await asyncio.wait({task}, timeout=PROGRESS_S)
            snap = await _progress(plan_id)
            if snap is None or snap == last or snap[0] != store.STEP_RUNNING:
                continue
            last = snap
            await _publish(
                ctx,
                PROGRESS_EVENT,
                {
                    "action": spec.id,
                    "title": spec.title_ru,
                    "plan_id": plan_id,
                    "status": "running",
                    "progress": snap[1],
                    "note": snap[2],
                },
            )
    except asyncio.CancelledError:
        with contextlib.suppress(Exception):
            await executor.request_stop(plan_id, operator)
        raise


async def _undo(plan_id: str) -> dict | None:
    async with store.sessions()() as s:
        ids = (
            await s.scalars(
                select(store.ActionSnapshot.id).where(
                    store.ActionSnapshot.plan_id == plan_id,
                    store.ActionSnapshot.restorable.is_(True),
                    store.ActionSnapshot.restored_at.is_(None),
                )
            )
        ).all()
    if not ids:
        return None
    return {"action": "history.rollback", "snapshot_ids": [int(i) for i in ids]}


_NAMES_FAILURE = re.compile(r"\bне выполнен[аы]?:")
_FAIL_RU = {
    store.STEP_UNKNOWN: "результат неизвестен",
    store.STEP_CANCELLED: "остановлено",
    store.STEP_SKIPPED: "шаг пропущен",
}


async def _outcome(plan_id: str, spec) -> dict:
    view = await service.plan_view(plan_id)
    step = (view.get("steps") or [{}])[0]
    status = str(step.get("status") or view.get("status") or "")
    cost = step.get("cost") or {}
    actual, high = cost.get("actual"), cost.get("high")
    if status == store.STEP_SUCCEEDED:
        if actual is not None:
            spent, source = to_usd(actual), "actual"
        elif spec.spends_money and high:
            spent, source = to_usd(high), "estimate"
        else:
            spent, source = 0.0, "free"
        return {
            "status": "ok",
            "plan_id": plan_id,
            "result": step.get("result"),
            "cost": spent or 0.0,
            "cost_source": source,
            "undo": await _undo(plan_id),
        }
    error = str(step.get("error") or view.get("error") or "").strip()
    head = _FAIL_RU.get(status, "шаг не выполнен")
    if status not in _FAIL_RU and _NAMES_FAILURE.search(error):
        head = ""  # итог шага уже говорит «не выполнен» с именем проекта и шага
    return {
        "status": "failed",
        "plan_id": plan_id,
        "outcome": status or "failed",
        "error": f"{head}: {error}" if error and head else (error or head),
        "cost": to_usd(actual) or 0.0,
        "cost_source": "actual" if actual is not None else "none",
    }


def _summary(spec, outcome: dict) -> str:
    if outcome.get("status") == "ok":
        text = f"Готово: {spec.title_ru}."
        if spec.spends_money and outcome.get("cost"):
            word = "потрачено" if outcome.get("cost_source") == "actual" else "по оценке до"
            text += f" {word.capitalize()} ${float(outcome['cost']):.2f}."
        if outcome.get("undo"):
            text += " Правку можно откатить."
        return text
    return f"Не получилось: {spec.title_ru}. {outcome.get('error') or ''}".strip()


async def _announce(ctx: dict, spec, outcome: dict) -> None:
    await _publish(
        ctx,
        PROGRESS_EVENT,
        {
            "action": spec.id,
            "title": spec.title_ru,
            "plan_id": outcome.get("plan_id"),
            "status": "succeeded" if outcome.get("status") == "ok" else "failed",
            "cost_usd": outcome.get("cost"),
            "cost_source": outcome.get("cost_source"),
            "error": outcome.get("error"),
        },
    )
    await _publish(
        ctx,
        RESULT_EVENT,
        {"role": "assistant", "kind": "action_result", "action": spec.id, "text": _summary(spec, outcome)},
    )


def _failed(text: str) -> dict:
    return {"status": "failed", "error": text, "cost": 0.0}


async def run_bridged(spec, params: dict, ctx: dict, core_approval: dict | None) -> dict:
    ctx = ctx if isinstance(ctx, dict) else {}
    app = ctx.get("app")
    task_id = ctx.get("task_id")
    if spec.needs_approval:
        change_id = (core_approval or {}).get("change_id")
        plan_id = await bound_plan(change_id, spec.id)
        if not plan_id or not await plan_matches(plan_id, spec.id, params):
            return _failed("нет подтверждения человека для этого шага: одобрите карточку в чате")
        operator = await _approver(plan_id) or _creator(task_id).id
    else:
        try:
            plan_id, preview = await service.create_plan(
                app,
                _steps(spec.id, params),
                operator=_creator(task_id),
                request_text=_request_text(spec, ctx.get("goal")),
            )
        except Exception as exc:
            return _failed(f"шаг не подготовлен: {type(exc).__name__}: {exc}")
        if plan_id is None:
            return _failed(
                "; ".join(str(e) for e in (preview.get("errors") or [])[:5]) or "шаг не прошёл проверку"
            )
        operator = _creator(task_id).id
    try:
        await service.prepare_run(plan_id, approval.OperatorIdentity(operator))
    except (service.RunRefused, approval.ApprovalError, LookupError) as exc:
        return _failed(str(exc))
    except Exception as exc:
        return _failed(f"{type(exc).__name__}: {exc}")
    task = executor.launch(app, plan_id, operator)
    await _follow(task, plan_id, spec, ctx, operator)
    outcome = await _outcome(plan_id, spec)
    await _announce(ctx, spec, outcome)
    return outcome


RESTART_STEP_ERROR = (
    "сервер перезапустился во время шага: итог и траты неизвестны, сверьте их и снимите резерв в Регистраторе"
)


async def close_interrupted_plans() -> list[str]:
    async with store.sessions()() as s:
        ids = (
            await s.scalars(
                select(store.ActionPlan.id).where(
                    store.ActionPlan.status == store.PLAN_RUNNING,
                    store.ActionPlan.operator.like("orchestrator%"),
                )
            )
        ).all()
    closed = []
    for plan_id in ids:
        if await executor.close_interrupted(plan_id, "orchestrator", RESTART_STEP_ERROR):
            closed.append(plan_id)
    return closed


def human_operator(scope: dict) -> approval.OperatorIdentity | None:
    """Человек-оператор запроса по личности новой линии (``app.web.identity``).

    Наша авторизация (сессии, ORCH_API_TOKEN, X-VP-Operator) не перенесена.
    Режим учётных записей: любой клиент студии одобряет шаги СВОЕГО чата, id
    ``studio_user:<uuid>``. Чужую карточку он не видит: сессия, задача и план
    другого арендатора для него не существуют (RLS и арендатор в имени
    сессии), поэтому роль здесь не проверяется.
    Режим владельца (учётки выключены): локальный оператор, как остальной API.
    """
    from app.services.studio_auth import current_identity
    from app.settings import settings

    ident = scope.get("studio_identity") or current_identity()
    if ident is not None:
        return approval.OperatorIdentity("studio_user:" + str(ident.user_id), via_http=True)
    if settings.accounts_enabled:
        return None
    return approval.OperatorIdentity("local_web_operator", via_http=True)


def actor_of(scope: dict) -> str:
    person = human_operator(scope)
    return person.id if person is not None else "orch_api_client"


@contextlib.contextmanager
def human_verified():
    """Отметка «человек подтверждён» для ``approval._check_operator``.

    Личность уже проверена ``human_operator``. Флаг ``access_guard`` ставится,
    только если модуль перенесён; без него проверка токена оператора не действует.
    """
    try:
        from app.services import access_guard
    except ImportError:
        yield
        return
    marker = access_guard.operator_token_verified.set(True)
    try:
        yield
    finally:
        access_guard.operator_token_verified.reset(marker)


async def bound_plan_any(change_id: str | None) -> str | None:
    if not change_id:
        return None
    if _bound_key(change_id) in _BOUND:
        return _BOUND[_bound_key(change_id)]
    async with store.sessions()() as s:
        rows = (
            await s.scalars(
                select(store.ActionHistory)
                .where(store.ActionHistory.event == CARD_EVENT)
                .order_by(store.ActionHistory.id.desc())
                .limit(500)
            )
        ).all()
    for row in rows:
        if isinstance(row.params, dict) and row.params.get("change_id") == change_id:
            return row.plan_id
    return None


def session_project(runtime: Any, session_id: str) -> int | None:
    task_id = runtime.supervisor._session_task.get(session_id)
    if task_id is None:
        return None
    raw = (runtime.tasks.get(int(task_id)) or {}).get("project_id")
    try:
        value = int(raw) if raw is not None else 0
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


async def stop_session_project(app: Any, project_id: int, task_id: Any) -> dict:
    from app.orchestrator import actions as orch_actions

    ctx = {"app": app, "task_id": task_id}
    try:
        done = await orch_actions.execute("project.stop", {"project_id": int(project_id)}, ctx)
    except Exception as exc:
        logger.exception("orchestrator kill: project {} not stopped ({})", project_id, type(exc).__name__)
        return {"ok": False, "stopped": False, "message": "Проект не остановлен: ошибка сервера."}
    result = done.get("result") if isinstance(done, dict) else None
    result = result if isinstance(result, dict) else {}
    inner = result.get("result") if isinstance(result.get("result"), dict) else result
    ok = isinstance(done, dict) and done.get("status") == "ok"
    inner_dict: dict[str, Any] = inner if isinstance(inner, dict) else {}
    return {
        "ok": ok,
        "stopped": bool(inner_dict.get("stopped")) if ok else False,
        "message": str(inner_dict.get("message") or done.get("error") or "") if isinstance(done, dict) else "",
    }


def session_for(runtime: Any, change_id: str) -> tuple[str, int | None]:
    owner = runtime.supervisor._appr_owner.get(change_id)
    if owner is not None:
        return str(owner[1]), (int(owner[0]) or None)
    match = _CHANGE_ID.fullmatch(change_id or "")
    if match is not None and int(match.group(1)) > 0:
        row = runtime.tasks.get(int(match.group(1)))
        if row is not None:
            return str(row["session_id"]), int(row["id"])
    raise DecisionError(404, "approval not found")


async def card_payload(runtime: Any, session_id: str, change_id: str | None) -> dict | None:
    if not change_id:
        return None
    for event in reversed(await runtime.bus.replay_from(session_id, 0)):
        if event.type == "approval_required" and event.payload.get("change_id") == change_id:
            return dict(event.payload)
    return None


async def _pending_step(runtime: Any, task_id: int | None, action: str | None, change_id: str | None = None):
    if task_id is None or not action:
        return None
    data = await asyncio.to_thread(runtime.checkpoints.load_latest, int(task_id))
    if data is None:
        return None
    position = int(data.position)
    # Карточка шага параллельной группы: чекпоинт стоит на начале группы, шаг дальше.
    aimed = getattr(runtime.supervisor, "card_position", None)
    if callable(aimed):
        position = int(aimed(int(task_id), change_id, list(data.plan), position))
    if position >= len(data.plan) or data.plan[position].action != action:
        return None
    return data.plan[position]


async def _group_steps(
    runtime: Any, task_id: int | None, change_id: str | None
) -> list[tuple[int, Any]] | None:
    """Шаги групповой карточки из чекпоинта. None — карточка не групповая."""
    probe = getattr(runtime.supervisor, "card_group", None)
    if task_id is None or not change_id or not callable(probe):
        return None
    data = await asyncio.to_thread(runtime.checkpoints.load_latest, int(task_id))
    if data is None:
        return None
    plan = list(data.plan)
    positions = probe(int(task_id), change_id, plan)
    if positions is None:
        return None
    return [(at, plan[at]) for at in positions]


async def _approve_group(
    app: Any,
    *,
    change_id: str,
    task_id: Any,
    steps: list[tuple[int, Any]],
    card: dict,
    operator: approval.OperatorIdentity,
    goal: str = "",
) -> list[tuple[str, str]]:
    """Одно нажатие — подтверждение ядра каждому шагу группы, у каждого свой план и хэш.
    Отказ любого шага отзывает уже выпущенные этим вызовом: группа одобряется целиком или никак."""
    raw_group = card.get("group")
    group: dict[str, Any] = raw_group if isinstance(raw_group, dict) else {}
    raw_plan_ids = group.get("plan_ids")
    plan_ids: dict[str, Any] = raw_plan_ids if isinstance(raw_plan_ids, dict) else {}
    issued: list[tuple[str, str]] = []
    from app.orchestrator.agents.supervisor import group_step_change_id

    try:
        for at, step in steps:
            if not plan_required(step.action):
                continue
            own = group_step_change_id(change_id, at)
            plan_id, fresh = await _approve_card(
                app,
                change_id=own,
                task_id=task_id,
                action=step.action,
                params=dict(step.params),
                card_plan_id=plan_ids.get(str(at)),
                operator=operator,
                goal=goal,
            )
            if fresh:
                issued.append((plan_id, own))
    except BaseException:
        for plan_id, own in issued:
            await revoke(plan_id, own, operator.id)
        raise
    return issued


_APPROVE_LOCKS: dict[str, asyncio.Lock] = {}


async def approve(
    runtime: Any,
    app: Any,
    *,
    session_id: str,
    change_id: str | None,
    decision: str | None,
    retry_grant: bool,
    operator: approval.OperatorIdentity,
) -> tuple[Any, str | None]:
    """Решения одной сессии идут по очереди. Двойной клик раньше выпускал второе подтверждение,
    оно гасило первое, а отказанный второй grant отзывал и его: шаг падал без подтверждения."""
    lock = _APPROVE_LOCKS.setdefault(session_id, asyncio.Lock())
    async with lock:
        return await _approve_locked(
            runtime,
            app,
            session_id=session_id,
            change_id=change_id,
            decision=decision,
            retry_grant=retry_grant,
            operator=operator,
        )


async def _approve_locked(
    runtime: Any,
    app: Any,
    *,
    session_id: str,
    change_id: str | None,
    decision: str | None,
    retry_grant: bool,
    operator: approval.OperatorIdentity,
) -> tuple[Any, str | None]:
    supervisor = runtime.supervisor
    adopt = getattr(supervisor, "adopt_after_restart", None)
    if callable(adopt):
        await adopt(session_id, change_id)
    task_id = supervisor._session_task.get(session_id)
    if task_id is not None:
        await supervisor._load_appr_state(session_id, task_id)
        change_id = change_id or supervisor._appr_open.get(int(task_id))
    card = await card_payload(runtime, session_id, change_id)
    runs = retry_grant or (
        decision in (None, "approve") and str((card or {}).get("reason") or "") not in _NO_RUN_REASONS
    )
    plan_id = None
    fresh = False
    issued: list[tuple[str, str]] = []
    bridge_on = callable(getattr(supervisor.actions, "prepare_card", None))
    group = (
        await _group_steps(runtime, task_id, change_id) if runs and card is not None and bridge_on else None
    )
    if group is not None:
        if supervisor._appr_open.get(int(task_id)) != change_id and not retry_grant:
            raise DecisionError(409, "change_id is not current")
        goal = str((runtime.tasks.get(int(task_id)) or {}).get("goal") or "")
        with human_verified():
            issued = await _approve_group(
                app,
                change_id=change_id or "",
                task_id=task_id,
                steps=group,
                card=card or {},
                operator=operator,
                goal=goal,
            )
    elif runs and card is not None and bridge_on and change_id:
        step = await _pending_step(runtime, task_id, card.get("action"), change_id)
        if step is not None and plan_required(step.action):
            if supervisor._appr_open.get(int(task_id)) != change_id and not retry_grant:
                raise DecisionError(409, "change_id is not current")
            goal = str((runtime.tasks.get(int(task_id)) or {}).get("goal") or "")
            with human_verified():
                plan_id, fresh = await _approve_card(
                    app,
                    change_id=change_id,
                    task_id=task_id,
                    action=step.action,
                    params=dict(step.params),
                    card_plan_id=card.get("plan_id"),
                    operator=operator,
                    goal=goal,
                )
    try:
        result = await supervisor.grant_approval(
            session_id, decision, retry_grant=retry_grant, change_id=change_id
        )
    except BaseException:
        # Отзывается только подтверждение этого вызова: прежнее живое ждёт свой шаг.
        if plan_id and fresh:
            await revoke(plan_id, change_id, operator.id)
        for own_plan, own in issued:
            await revoke(own_plan, own, operator.id)
        raise
    if plan_id and retry_grant and isinstance(result, dict) and not result.get("executed"):
        await revoke(plan_id, change_id, operator.id)
        plan_id = None
    return result, plan_id


def _notice_payload(notice: dict) -> dict:
    title = str(notice.get("title") or "")
    text = str(notice.get("text") or "")
    return {
        **notice,
        "notice_id": notice.get("id"),
        "role": "system",
        "message": f"{title}. {text}".strip(". ") if text else title,
    }


def _seen_sync(session_id: str) -> set[int]:
    from sqlalchemy import text

    from app.orchestrator.db.migrations import connect

    conn = connect()
    try:
        rows: list[Any] = list(
            conn.execute(
                text(
                    "SELECT payload_json FROM orch_events WHERE session_id = :sid AND type = :type"
                    " ORDER BY seq DESC LIMIT 2000"
                ),
                {"sid": session_id, "type": NOTICE_EVENT},
            ).scalars()
        )
    finally:
        conn.close()
    found: set[int] = set()
    for raw in rows:
        try:
            payload = json.loads(raw or "{}")
        except ValueError:
            continue
        value = payload.get("notice_id") if isinstance(payload, dict) else None
        if isinstance(value, int):
            found.add(value)
    return found


async def _seen(session_id: str) -> set[int]:
    if session_id not in _SEEN:
        _SEEN[session_id] = await asyncio.to_thread(_seen_sync, session_id)
    return _SEEN[session_id]


def forget_seen() -> None:
    _SEEN.clear()
    _SEEN_LOCKS.clear()


def _for_project(notice: Any, project_id: int) -> bool:
    if not isinstance(notice, dict) or not isinstance(notice.get("id"), int):
        return False
    owner = notice.get("project_id")
    return owner is None or owner == project_id


async def forward_notice(bus: Any, session_id: str, notice: dict) -> bool:
    lock = _SEEN_LOCKS.setdefault(session_id, asyncio.Lock())
    async with lock:
        seen = await _seen(session_id)
        if notice["id"] in seen:
            return False
        await bus.publish(session_id, NOTICE_EVENT, _notice_payload(notice), task_id=None)
        seen.add(notice["id"])
        return True


async def _catch_up(bus: Any, session_id: str, project_id: int) -> None:
    from app.services import chat_notice

    seen = await _seen(session_id)
    cursor = max(seen, default=0)
    if cursor:
        items = await chat_notice.list_notices(project_id, cursor, 50)
    else:
        items = await chat_notice.list_notices(project_id, 0, NOTICE_BACKLOG, unread_only=True)
    for item in items:
        if _for_project(item, project_id):
            await forward_notice(bus, session_id, item)


async def _relay(bus: Any, session_id: str, project_id: int) -> None:
    from app.services import chat_notice
    from app.services.event_bus import get_bus

    async with get_bus().subscribe("global") as queue:
        await _catch_up(bus, session_id, project_id)
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=NOTICE_SWEEP_S)
            except TimeoutError:
                await _catch_up(bus, session_id, project_id)
                continue
            if not isinstance(event, dict) or event.get("type") != chat_notice.EVENT_TYPE:
                continue
            notice = event.get("notice")
            if _for_project(notice, project_id):
                await forward_notice(bus, session_id, notice)  # type: ignore[arg-type]


async def _guarded_relay(bus: Any, session_id: str, project_id: int) -> None:
    try:
        await _relay(bus, session_id, project_id)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("orch bridge: notice relay stopped for session {}", session_id)


@asynccontextmanager
async def notice_relay(bus: Any, session_id: str, project_id: int | None):
    if project_id is None:
        yield
        return
    key = (id(bus), session_id, int(project_id))
    entry = _RELAYS.get(key)
    if entry is None or entry["task"].done():
        entry = {
            "refs": 0,
            "task": asyncio.create_task(
                _guarded_relay(bus, session_id, int(project_id)), name=f"orch-notice-relay-{session_id}"
            ),
        }
        _RELAYS[key] = entry
    entry["refs"] += 1
    try:
        yield
    finally:
        entry["refs"] -= 1
        if entry["refs"] <= 0:
            entry["task"].cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await entry["task"]
            if _RELAYS.get(key) is entry:
                _RELAYS.pop(key, None)
