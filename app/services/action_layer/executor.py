from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from datetime import datetime, timedelta
from typing import Any

from loguru import logger
from sqlalchemy import select, update

from app.services import kill_switch
from app.services.action_layer import budget, store
from app.services.action_layer.context import ActionContext
from app.services.action_layer.messages_ru import validation_list_ru
from app.services.action_layer.spec import ActionRegistry, ActionSpec
from app.services.action_layer.spec import registry as default_registry

_TASKS: dict[str, asyncio.Task] = {}
_CURRENT: dict[str, dict] = {}
_RESULT_LIMIT = 20000
KILL_EXEMPT_ACTIONS = frozenset({"project.stop"})
_ORPHANS: dict[int, dict] = {}


def _env_float(name: str, default: float) -> float:
    try:
        return max(0.0, float(os.environ.get(name, default)))
    except (TypeError, ValueError):
        return default


def stop_grace_s() -> float:
    return _env_float("ACTION_LAYER_STOP_GRACE_S", 30.0)


def orphan_max_s() -> float:
    return _env_float("ACTION_LAYER_ORPHAN_MAX_S", 3600.0)


_CLAIMS: dict[int, dict] = {}
_CLAIM_TTL_S = 60.0


def claim_project(project_id: int | None, plan_id: str) -> str | None:
    if project_id is None:
        return None
    pid = int(project_id)
    cur = _CLAIMS.get(pid)
    if cur is not None and cur["plan_id"] != plan_id and time.monotonic() < cur["until"]:
        return cur["plan_id"]
    _CLAIMS[pid] = {"plan_id": plan_id, "until": time.monotonic() + _CLAIM_TTL_S}
    return None


def release_claim(plan_id: str) -> None:
    for pid in [k for k, v in _CLAIMS.items() if v["plan_id"] == plan_id]:
        _CLAIMS.pop(pid, None)


def orphan_busy(project_id: int | None) -> dict | None:
    if project_id is None:
        return None
    info = _ORPHANS.get(int(project_id))
    if info is None:
        return None
    if info.get("until") is not None and time.monotonic() >= info["until"]:
        _ORPHANS.pop(int(project_id), None)
        return None
    return info


async def orphan_busy_db(session, project_id: int | None, plan_id: str = "") -> dict | None:
    if project_id is None:
        return None
    since = _now() - timedelta(seconds=orphan_max_s())
    rows = (
        await session.execute(
            select(store.ActionHistory.plan_id, store.ActionHistory.action_id, store.ActionHistory.created_at)
            .join(store.ActionPlan, store.ActionPlan.id == store.ActionHistory.plan_id)
            .where(
                store.ActionPlan.project_id == int(project_id),
                store.ActionHistory.event.in_(("job_stop_after_failure", "job_stalled")),
                store.ActionHistory.outcome == "unconfirmed",
                store.ActionHistory.created_at >= since,
            )
            .order_by(store.ActionHistory.created_at.desc())
        )
    ).all()
    for pid_, action_id, created in rows:
        if pid_ == plan_id:
            continue
        released = await session.scalar(
            select(store.ActionHistory.id)
            .where(
                store.ActionHistory.plan_id == pid_,
                store.ActionHistory.event == "job_orphan_released",
                store.ActionHistory.created_at >= created,
            )
            .limit(1)
        )
        if released is None:
            return {"plan_id": pid_, "action_id": action_id, "from_journal": True}
    return None


async def _journal_orphan_released(plan_id: str, action_id: str) -> None:
    try:
        async with store.sessions()() as s:
            store.append_history(
                s,
                event="job_orphan_released",
                plan_id=plan_id,
                action_id=action_id,
                outcome="released",
                actor="system",
            )
            await s.commit()
    except Exception:
        logger.exception("action layer orphan release journal failed plan={}", plan_id)


class _Stopped(Exception):
    pass


class JobFailed(RuntimeError):
    """Фоновая задача завершилась ошибкой; текст ошибки — готовый итог для чата."""


class _KillSwitchStopped(Exception):
    pass


class _Stalled(Exception):
    def __init__(self, idle_s: float):
        self.idle_s = idle_s
        super().__init__(f"no progress for {idle_s:.0f}s")


def no_progress_timeout_s() -> float:
    from app.settings import settings

    return float(settings.action_layer_no_progress_timeout_s)


def progress_probe_s() -> float:
    from app.settings import settings

    return float(settings.action_layer_progress_probe_s)


async def project_activity(ctx: ActionContext, project_id: int | None) -> str | None:
    if project_id is None:
        return None
    try:
        proj = await ctx.call("GET", f"/projects/{int(project_id)}")
        run = await ctx.call("GET", f"/projects/{int(project_id)}/run")
    except Exception as exc:
        logger.debug("action layer activity probe failed project={}: {}", project_id, exc)
        return None
    # Ручки новой линии могут ответить не объектом (404-текст, другой контракт).
    proj = proj if isinstance(proj, dict) else {}
    run = run if isinstance(run, dict) else {}
    rows = sorted(
        (
            str(r.get("node_key")),
            str(r.get("status")),
            str(r.get("progress")),
            str(r.get("progress_text")),
            str(r.get("updated_at")),
            str(r.get("finished_at")),
        )
        for r in ((run or {}).get("node_runs") or [])
        if isinstance(r, dict)
    )
    raw = json.dumps(
        {"status": (proj or {}).get("status"), "updated": (proj or {}).get("updated_at"), "nodes": rows},
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def slot_wait_max_s() -> float:
    """Сколько проект может ждать свободного места в очереди воркера, пока шаг ещё жив."""
    return _env_float("ACTION_LAYER_SLOT_WAIT_MAX_S", 12 * 3600.0)


class _SlotWaitTooLong(Exception):
    def __init__(self, wait_s: float):
        self.wait_s = wait_s
        super().__init__(f"waited for a worker slot {wait_s:.0f}s")


def worker_slots() -> tuple[int, int]:
    """(занято, всего) мест воркера в этом процессе: сколько проектов идут одновременно."""
    from app.services.gen_queue import worker_max_parallel
    from app.services.step_cancel import active_advance_count

    return active_advance_count(), max(1, int(worker_max_parallel()))


# Шаги, которые проект делает руками воркера: только их воркер может держать в очереди.
WORKER_QUEUED_ACTIONS: frozenset[str] = frozenset(
    {"step.run_step", "node.run_step", "project.resume", "project.continue_pipeline"}
)
# Причины, которые воркер пишет в runtime_wait; очередь - только первые две.
QUEUE_REASONS: frozenset[str] = frozenset({"parallel_slots_full", "queue_predecessor"})


def goes_through_worker(spec: ActionSpec) -> bool:
    return spec.id in WORKER_QUEUED_ACTIONS or "worker_queue" in (spec.tags or ())


def _worker_would_take(project: dict | None) -> bool:
    """Проект в running-статусе, без ручного стопа и без паузы после ошибок."""
    if not isinstance(project, dict):
        return False
    from types import SimpleNamespace

    from app.models import ProjectStatus
    from app.services.gen_queue_run import is_user_stopped
    from app.services.project_state import is_running_status
    from app.services.step_failure_policy import is_sleeping

    try:
        if not is_running_status(ProjectStatus(str(project.get("status")))):
            return False
        meta = project.get("meta") if isinstance(project.get("meta"), dict) else {}
        shell = SimpleNamespace(meta=meta)
        return not (is_user_stopped(shell) or is_sleeping(shell))
    except Exception:  # noqa: BLE001
        return False


def project_waits_for_slot(
    project_id: int | None, project: dict | None = None
) -> tuple[int, int, str] | None:
    """Проект запущен, но воркер его ещё не взял, потому что он в очереди.

    Возвращает (занято, всего, причина) или None, если проект идёт или ждёт не очереди.
    Причину берём у воркера (то, что он пишет в runtime_wait): места заняты или впереди
    в очереди генерации есть проекты. Пауза после ошибок и ручной стоп - не очередь.
    Пока воркер этот проект ещё не смотрел, очередью считаем только running-проект
    (``project``), когда все места заняты.
    Ожидание места - не простой: сторож хода и сроки шага его не считают.
    """
    if project_id is None:
        return None
    from app.services.step_cancel import is_generation_active, worker_wait_reason

    try:
        if is_generation_active(int(project_id)):
            return None
        reason = worker_wait_reason(int(project_id))
        busy, total = worker_slots()
    except Exception as exc:  # noqa: BLE001
        logger.debug("action layer slot probe failed project={}: {}", project_id, exc)
        return None
    if reason in QUEUE_REASONS:
        return busy, total, reason
    if reason is not None or busy < total:
        return None
    return (busy, total, "parallel_slots_full") if _worker_would_take(project) else None


async def _slot_wait(ctx: ActionContext, spec: ActionSpec, cur: dict) -> tuple[int, int, str] | None:
    """Ждёт ли проект шага очереди воркера. Проект читаем не чаще раза в probe_s."""
    pid = cur.get("project_id")
    if pid is None or not goes_through_worker(spec):
        return None
    from app.services.step_cancel import is_generation_active, worker_wait_reason

    project = None
    try:
        need = (
            not is_generation_active(int(pid))
            and worker_wait_reason(int(pid)) is None
            and worker_slots()[0] >= worker_slots()[1]
        )
    except Exception:  # noqa: BLE001
        need = False
    if need:
        now = time.monotonic()
        seen = cur.get("queue_probe")
        if seen is None or now - seen[0] >= progress_probe_s():
            try:
                got = await ctx.call("GET", f"/projects/{int(pid)}")
            except Exception as exc:  # noqa: BLE001
                logger.debug("action layer queue probe failed project={}: {}", pid, exc)
                got = None
            seen = (now, got if isinstance(got, dict) else None)
            cur["queue_probe"] = seen
        project = seen[1]
    return project_waits_for_slot(pid, project)


def _probed(cur: dict) -> dict | None:
    seen = cur.get("queue_probe")
    return seen[1] if seen else None


def _generation_active(project_id: int | None) -> bool:
    if project_id is None:
        return False
    from app.services.step_cancel import is_generation_active

    try:
        return bool(is_generation_active(int(project_id)))
    except Exception:  # noqa: BLE001
        return False


# План -> когда его проект встал ждать места (по порядку прихода, для «N из M»).
_SLOT_WAITERS: dict[str, float] = {}
# План -> арендатор, от имени которого он ждёт. Место в очереди («2 из 3») считается
# только среди планов того же арендатора: чужие ожидающие планы — чужие сведения.
# Занято и предел (общая параллель студии) остаются общими.
_SLOT_WAITER_TENANTS: dict[str, str | None] = {}


def _tenant_now() -> str | None:
    from app.services.tenant import current_tenant

    return current_tenant()


def _note_slot_waiter(plan_id: str, now: float) -> None:
    _SLOT_WAITERS.setdefault(plan_id, now)
    _SLOT_WAITER_TENANTS.setdefault(plan_id, _tenant_now())


def _drop_slot_waiter(plan_id: str) -> None:
    _SLOT_WAITERS.pop(plan_id, None)
    _SLOT_WAITER_TENANTS.pop(plan_id, None)


def slot_wait_text(plan_id: str, total: int, reason: str = "parallel_slots_full") -> str:
    tenant = _SLOT_WAITER_TENANTS.get(plan_id, _tenant_now())
    order = sorted(
        (pid for pid in _SLOT_WAITERS if _SLOT_WAITER_TENANTS.get(pid) == tenant),
        key=lambda pid: _SLOT_WAITERS[pid],
    )
    place = order.index(plan_id) + 1 if plan_id in order else len(order) + 1
    waiting = max(len(order), place)
    why = (
        "впереди в очереди генерации есть проекты"
        if reason == "queue_predecessor"
        else f"одновременно идут не больше {total}"
    )
    return (
        f"в очереди, ждет свободного места ({place} из {waiting}); {why}, "
        "время в очереди не считается простоем"
    )


class ProgressWatch:
    def __init__(
        self, project_id: int | None, *, timeout_s: float | None = None, probe_s: float | None = None
    ) -> None:
        self.project_id = project_id
        self.timeout_s = no_progress_timeout_s() if timeout_s is None else float(timeout_s)
        self.probe_s = progress_probe_s() if probe_s is None else float(probe_s)
        self.last_change = time.monotonic()
        self.last_probe = float("-inf")
        self.mark: Any = None
        self.activity: str | None = None

    def _touch(self, mark: Any) -> None:
        if mark != self.mark:
            if self.mark is not None:
                self.last_change = time.monotonic()
            self.mark = mark

    async def observe(self, ctx: ActionContext, state: Any) -> None:
        now = time.monotonic()
        if self.project_id is not None and now - self.last_probe >= self.probe_s:
            self.last_probe = now
            got = await project_activity(ctx, self.project_id)
            if got is not None:
                self.activity = got
        self._touch(
            (
                getattr(state, "progress", None),
                getattr(state, "note", ""),
                getattr(state, "activity", None),
                self.activity,
            )
        )

    def hold(self) -> None:
        """Проект ждёт места в очереди: отсчёт простоя начинается заново, когда он пойдёт."""
        self.last_change = time.monotonic()
        self.mark = None
        self.last_probe = float("-inf")

    def idle_s(self) -> float:
        return time.monotonic() - self.last_change

    def stalled(self) -> bool:
        return self.idle_s() > self.timeout_s


def is_live(plan_id: str) -> bool:
    task = _TASKS.get(plan_id)
    return task is not None and not task.done()


def live_plan_ids() -> list[str]:
    return [pid for pid in list(_TASKS) if is_live(pid)]


def safe_json(obj: Any, limit: int = _RESULT_LIMIT) -> Any:
    try:
        raw = json.dumps(obj, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return {"unserializable": str(obj)[:500]}
    if len(raw) > limit:
        return {"truncated": True, "preview": raw[:2000]}
    return json.loads(raw)


def _now() -> datetime:
    return datetime.utcnow()


def launch(app: Any, plan_id: str, operator: str, *, registry: ActionRegistry | None = None) -> asyncio.Task:
    if is_live(plan_id):
        return _TASKS[plan_id]
    task = asyncio.create_task(
        _run_plan(app, plan_id, operator, registry or default_registry), name=f"action-layer-{plan_id}"
    )
    _TASKS[plan_id] = task
    for info in _CLAIMS.values():
        if info["plan_id"] == plan_id:
            info["until"] = float("inf")
    return task


async def wait(plan_id: str) -> None:
    task = _TASKS.get(plan_id)
    if task is not None:
        await asyncio.shield(task)


async def drain_background() -> None:
    """Отменить планы и сторожей сирот и дождаться их, пока сессия ещё жива."""
    pending: list[asyncio.Task] = []
    for task in list(_TASKS.values()):
        if not task.done():
            task.cancel()
            pending.append(task)
    for info in list(_ORPHANS.values()):
        task = info.get("task")
        if isinstance(task, asyncio.Task) and not task.done():
            task.cancel()
            pending.append(task)
    if pending:
        _done, still = await asyncio.wait(pending, timeout=5)
        if still:
            logger.warning(
                "action layer: drain_background timeout, осталось задач: {}",
                len(still),
            )
    _TASKS.clear()
    _ORPHANS.clear()


async def _finish(plan_id: str, status: str, *, error: str = "", reason: str = "") -> None:
    async with store.sessions()() as s:
        plan = await s.get(store.ActionPlan, plan_id)
        if plan is None:
            return
        plan.status = status
        plan.error = error[:2000]
        plan.stop_reason = reason
        plan.updated_at = plan.finished_at = _now()
        if status in (store.PLAN_FAILED, store.PLAN_STOPPED):
            await s.execute(
                update(store.ActionPlanStep)
                .where(
                    store.ActionPlanStep.plan_id == plan_id,
                    store.ActionPlanStep.status == store.STEP_PENDING,
                )
                .values(status=store.STEP_SKIPPED)
            )
        store.append_history(
            s,
            event="plan_finished",
            plan_id=plan_id,
            actor=plan.operator,
            outcome=status,
            error=error or reason,
        )
        await s.commit()


async def close_interrupted(plan_id: str, operator: str, error: str) -> bool:
    """Закрывает план, прерванный рестартом и никем не возобновляемый.

    Выполнявшийся шаг становится ``unknown``: резерв не списывается, а попадает
    в список незакрытых резервов, где его снимает человек после сверки.
    """
    if is_live(plan_id):
        return False
    async with store.sessions()() as s:
        plan = await s.get(store.ActionPlan, plan_id)
        if plan is None or plan.status != store.PLAN_RUNNING:
            return False
        running = (
            await s.scalars(
                select(store.ActionPlanStep).where(
                    store.ActionPlanStep.plan_id == plan_id, store.ActionPlanStep.status == store.STEP_RUNNING
                )
            )
        ).all()
    for step in running:
        await _close_step(
            plan_id,
            step,
            store.STEP_UNKNOWN,
            error,
            "step_unknown",
            operator,
            (step.cost_low, step.cost_high),
        )
    await _finish(plan_id, store.PLAN_FAILED, error=error, reason="interrupted_by_restart")
    return True


async def _stop_requested(plan_id: str) -> bool:
    async with store.sessions()() as s:
        return bool(
            await s.scalar(select(store.ActionPlan.stop_requested).where(store.ActionPlan.id == plan_id))
        )


async def _kill_switch_on() -> bool:
    async with store.sessions()() as ks:
        return await kill_switch.is_active(ks)


async def _run_plan(app: Any, plan_id: str, operator: str, registry: ActionRegistry) -> None:
    try:
        async with store.sessions()() as s:
            steps = (
                await s.scalars(
                    select(store.ActionPlanStep)
                    .where(store.ActionPlanStep.plan_id == plan_id)
                    .order_by(store.ActionPlanStep.idx)
                )
            ).all()
            plan = await s.get(store.ActionPlan, plan_id)
            if plan is None:
                return
            store.append_history(
                s,
                event="plan_started",
                plan_id=plan_id,
                actor=operator,
                request_text=plan.request_text,
                outcome="running",
            )
            await s.commit()
        for step in steps:
            if await _stop_requested(plan_id):
                await _finish(plan_id, store.PLAN_STOPPED, reason="operator_stop")
                return
            outcome = await _run_step(app, plan_id, step.id, operator, registry)
            if outcome == "ok":
                continue
            if outcome == "blocked":
                await _finish(
                    plan_id,
                    store.PLAN_STOPPED,
                    reason=budget.KILL_SWITCH_REASON,
                    error="аварийный стоп включён оператором",
                )
            elif outcome == "stopped":
                await _finish(plan_id, store.PLAN_STOPPED, reason="operator_stop")
            else:
                await _finish(plan_id, store.PLAN_FAILED, error=f"шаг {step.idx + 1}: {outcome}")
            return
        await _finish(plan_id, store.PLAN_SUCCEEDED)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.exception("action layer plan {} crashed", plan_id)
        await _finish(plan_id, store.PLAN_FAILED, error=f"внутренняя ошибка: {type(exc).__name__}: {exc}")
    finally:
        _TASKS.pop(plan_id, None)
        _CURRENT.pop(plan_id, None)
        release_claim(plan_id)


async def _step_update(step_id: int, **values: Any) -> None:
    # Один UPDATE по id — повтор безопасен. Несколько проектов параллельно держат базу занятой.
    for attempt in range(5):
        try:
            async with store.sessions()() as s:
                await s.execute(
                    update(store.ActionPlanStep).where(store.ActionPlanStep.id == step_id).values(**values)
                )
                await s.commit()
            return
        except Exception as exc:  # noqa: BLE001
            if "database is locked" not in str(exc) or attempt == 4:
                raise
            await asyncio.sleep(0.2 * (attempt + 1))


async def _close_step(
    plan_id: str,
    step: store.ActionPlanStep,
    status: str,
    error: str,
    event: str,
    operator: str,
    estimate: tuple[float | None, float | None] = (None, None),
    actual: float | None = None,
) -> None:
    values: dict[str, Any] = {"status": status, "error": error[:2000], "finished_at": _now()}
    if actual is not None:
        values["cost_actual"] = actual
    async with store.sessions()() as s:
        await s.execute(
            update(store.ActionPlanStep).where(store.ActionPlanStep.id == step.id).values(**values)
        )
        store.append_history(
            s,
            event=event,
            plan_id=plan_id,
            step_index=step.idx,
            actor=operator,
            action_id=step.action_id,
            params=step.params,
            outcome=status,
            error=error,
            cost_estimate=estimate,
            cost_recorded=actual,
        )
        await s.commit()


async def verify_attachments(params: dict) -> str | None:
    from app.services.action_layer import attachments as att

    path = params.get("source_path")
    if not isinstance(path, str) or not path.replace("\\", "/").startswith(att.ATTACH_DIR + "/"):
        return None
    rel = path.replace("\\", "/")
    async with store.sessions()() as s:
        row = await s.scalar(select(att.ActionAttachment).where(att.ActionAttachment.rel_path == rel))
    if row is None:
        return "вложение не найдено в реестре загрузок: подмена пути или файл не загружался"
    try:
        real = att.abs_path(rel)
    except att.AttachmentError as exc:
        return str(exc)

    def _digest() -> tuple[str, int] | None:
        if not real.is_file():
            return None
        h, size = hashlib.sha256(), 0
        with real.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
                size += len(chunk)
        return h.hexdigest(), size

    got = await asyncio.to_thread(_digest)
    if got is None:
        return "файл вложения не найден на диске"
    if got[0] != row.sha256 or got[1] != int(row.size):
        return "файл вложения изменился после загрузки (sha256 не совпадает): запуск отклонён"
    return None


async def _settle_step(
    plan_id: str,
    step: store.ActionPlanStep,
    spec: ActionSpec,
    reserved: float,
    t0: datetime,
    reported: float | None,
) -> float | None:
    if not spec.spends_money and reported is None:
        return None
    if spec.deferred_spend:
        logger.info(
            "action layer: reserve of plan={} step={} ({}) is held, spend happens in the worker",
            plan_id,
            step.idx,
            spec.id,
        )
        return None
    try:
        async with store.sessions()() as s:
            actual, native = reported, False
            if actual is None:
                pid = await budget._plan_project(s, plan_id)
                actual = await budget.measure_native_credits(s, project_id=pid, since=t0)
                native = actual is not None
                if actual is None and reserved:
                    unknown, gap = await budget.native_unknown_cost_rows(s, project_id=pid, since=t0)
                    if unknown and not gap:
                        await budget.settle(
                            s,
                            key=budget.session_key(plan_id),
                            plan_id=plan_id,
                            step_index=step.idx,
                            reserved=reserved,
                            actual=0.0,
                            native_counted=True,
                        )
                        await s.commit()
                        return None
            await budget.settle(
                s,
                key=budget.session_key(plan_id),
                plan_id=plan_id,
                step_index=step.idx,
                reserved=reserved,
                actual=actual,
                native_counted=native,
            )
            await s.commit()
        return actual
    except Exception:
        logger.exception("action layer settle failed plan={} step={}", plan_id, step.idx)
        return None


async def _abort_job(plan_id: str, spec: ActionSpec, cur: dict) -> bool:
    await _call_stop(plan_id, cur)
    if cur.get("start") is None:
        return False
    deadline = time.monotonic() + stop_grace_s()
    while True:
        try:
            state = await spec.poll(cur["ctx"], cur["params"], cur["start"])
            if state.done:
                return True
        except Exception as exc:
            logger.warning("action layer poll after stop failed plan={}: {}", plan_id, exc)
        if time.monotonic() >= deadline:
            return False
        await asyncio.sleep(min(float(spec.poll_interval_s), 1.0))


def _hold_project(project_id: int | None, plan_id: str, spec: ActionSpec, cur: dict, on_done) -> None:
    if project_id is None:
        return
    pid = int(project_id)
    info = {
        "plan_id": plan_id,
        "action_id": spec.id,
        "until": time.monotonic()
        + (orphan_max_s() if cur.get("start") is not None else min(120.0, orphan_max_s())),
    }
    _ORPHANS[pid] = info

    async def _watch() -> None:
        try:
            while cur.get("start") is not None and time.monotonic() < info["until"]:
                await asyncio.sleep(max(0.2, min(float(spec.poll_interval_s), 5.0)))
                try:
                    state = await spec.poll(cur["ctx"], cur["params"], cur["start"])
                except Exception as exc:
                    logger.warning("orphan poll failed plan={}: {}", plan_id, exc)
                    continue
                if state.done:
                    await on_done(state)
                    await _journal_orphan_released(plan_id, spec.id)
                    return
            while time.monotonic() < info["until"]:
                await asyncio.sleep(0.5)
        finally:
            if _ORPHANS.get(pid) is info:
                _ORPHANS.pop(pid, None)

    task = asyncio.create_task(_watch(), name=f"action-layer-orphan-{plan_id}")
    info["task"] = task


async def _run_step(app: Any, plan_id: str, step_id: int, operator: str, registry: ActionRegistry) -> str:
    async with store.sessions()() as s:
        step = await s.get(store.ActionPlanStep, step_id)
        plan = await s.get(store.ActionPlan, plan_id)
        statuses = {
            r.idx: r.status
            for r in (
                await s.scalars(select(store.ActionPlanStep).where(store.ActionPlanStep.plan_id == plan_id))
            ).all()
        }
        request_text = plan.request_text if plan else ""
    if step.status == store.STEP_SUCCEEDED:
        return "ok"
    spec = registry.find(step.action_id)
    if spec is None:
        await _close_step(plan_id, step, store.STEP_FAILED, "неизвестное действие", "step_failed", operator)
        return "неизвестное действие"

    if step.status == store.STEP_RUNNING:
        if spec.idempotent or spec.risk == "read":
            await _step_update(step.id, status=store.STEP_PENDING)
        else:
            await _close_step(
                plan_id,
                step,
                store.STEP_UNKNOWN,
                "результат неизвестен после прерывания, нужен ручной разбор",
                "step_unknown",
                operator,
            )
            return "результат неизвестен после прерывания, нужен ручной разбор"

    from app.services.action_layer.plan import admin_only_message, admin_only_refused

    if admin_only_refused(spec.id, step.params):
        refusal = admin_only_message(spec.id)
        await _close_step(plan_id, step, store.STEP_FAILED, refusal, "step_failed", operator)
        return refusal

    if spec.id not in KILL_EXEMPT_ACTIONS and await _kill_switch_on():
        async with store.sessions()() as s:
            store.append_history(
                s,
                event="kill_switch_block",
                plan_id=plan_id,
                step_index=step.idx,
                actor=operator,
                action_id=step.action_id,
                outcome="blocked",
                error=budget.KILL_SWITCH_REASON,
            )
            await s.commit()
        return "blocked"

    for dep in step.depends_on or []:
        if statuses.get(dep) != store.STEP_SUCCEEDED:
            await _close_step(
                plan_id,
                step,
                store.STEP_SKIPPED,
                f"шаг {dep + 1}, от которого зависит этот, не выполнен",
                "step_skipped",
                operator,
            )
            return f"не выполнен шаг {dep + 1}, от которого зависит этот"

    errors = registry.validate_params(spec.id, step.params)
    est = spec.estimate(step.params)
    est_pair = (est.low, est.high)
    ctx = ActionContext(
        app=app, operator=operator, plan_id=plan_id, step_index=step.idx, idempotency_key=step.idempotency_key
    )
    if errors:
        await _close_step(
            plan_id,
            step,
            store.STEP_FAILED,
            "; ".join(validation_list_ru(errors)),
            "step_failed",
            operator,
            est_pair,
        )
        return "неверные параметры"
    for pre in spec.preconditions:
        try:
            reason = await pre.check(ctx.readonly_copy(), step.params)
        except Exception as exc:
            reason = f"проверка не выполнена: {type(exc).__name__}: {exc}"
        if reason:
            await _close_step(
                plan_id,
                step,
                store.STEP_FAILED,
                f"условие {pre.id}: {reason}",
                "step_failed",
                operator,
                est_pair,
            )
            return f"не выполнено условие ({pre.id})"

    bad_file = await verify_attachments(step.params or {})
    if bad_file:
        await _close_step(plan_id, step, store.STEP_FAILED, bad_file, "step_failed", operator, est_pair)
        return "вложение изменено или не найдено"

    reserved = 0.0
    key = budget.session_key(plan_id)
    if spec.spends_money:
        await mark_stale_reserves()
    async with store.sessions()() as s:
        try:
            if spec.spends_money:
                reserved = await budget.reserve(
                    s, key=key, plan_id=plan_id, step_index=step.idx, estimate=est
                )
        except budget.BudgetError as exc:
            await s.rollback()
            store.append_history(
                s,
                event="budget_block",
                plan_id=plan_id,
                step_index=step.idx,
                actor=operator,
                action_id=step.action_id,
                params=step.params,
                outcome="blocked",
                error=str(exc),
                cost_estimate=est_pair,
            )
            await s.execute(
                update(store.ActionPlanStep)
                .where(store.ActionPlanStep.id == step.id)
                .values(status=store.STEP_FAILED, error=str(exc)[:2000], finished_at=_now())
            )
            await s.commit()
            return f"ограничение бюджета ({exc.code})"
        claimed = await s.execute(
            update(store.ActionPlanStep)
            .where(store.ActionPlanStep.id == step.id, store.ActionPlanStep.status == store.STEP_PENDING)
            .values(
                status=store.STEP_RUNNING,
                attempts=store.ActionPlanStep.attempts + 1,
                started_at=_now(),
                cost_low=est.low,
                cost_high=est.high,
            )
        )
        if claimed.rowcount != 1:
            await s.rollback()
            return "шаг уже взят в работу в другом месте"
        store.append_history(
            s,
            event="step_started",
            plan_id=plan_id,
            step_index=step.idx,
            actor=operator,
            request_text=request_text,
            action_id=step.action_id,
            params=step.params,
            outcome="running",
            cost_estimate=est_pair,
        )
        await s.commit()

    async def sink(fraction: float | None, note: str) -> None:
        # Прогресс — подсказка интерфейсу: занятая база не должна ронять сам шаг.
        try:
            await _step_update(step.id, progress=fraction, progress_note=note[:500])
        except Exception as exc:  # noqa: BLE001
            if "database is locked" not in str(exc):
                raise
            logger.warning("action layer step {}: progress skipped, database is locked", step.id)

    ctx.progress_sink = sink
    t0 = _now()
    project_id = (await _plan_project(plan_id)) if spec.is_job else None
    cur = {
        "ctx": ctx,
        "spec": spec,
        "params": step.params,
        "stop_sent": False,
        "start": None,
        "job_finished": False,
        "project_id": project_id,
    }
    _CURRENT[plan_id] = cur
    try:
        result, credits = await _execute(plan_id, spec, ctx, step.params, cur)
    except TimeoutError:
        force = not spec.spends_money
        confirmed = await _abort_after_failure(
            plan_id, step, spec, cur, project_id, reserved, t0, operator, est_pair, force=force
        )
        actual = await _settle_if_done(plan_id, step, spec, reserved, t0, confirmed)
        tail = "" if confirmed else "; остановка серверной задачи не подтверждена, проект занят до её конца"
        await _close_step(
            plan_id,
            step,
            store.STEP_UNKNOWN,
            "шаг не уложился во время, результат неизвестен" + tail,
            "step_unknown",
            operator,
            est_pair,
            actual,
        )
        return "время вышло, результат неизвестен"
    except _Stalled as stall:
        minutes = max(1, round(stall.idle_s / 60))
        if not spec.spends_money:
            confirmed = await _abort_after_failure(
                plan_id, step, spec, cur, project_id, reserved, t0, operator, est_pair, force=True
            )
            actual = await _settle_if_done(plan_id, step, spec, reserved, t0, confirmed)
            tail = "" if confirmed else "; остановка задачи не подтверждена, проект занят до её конца"
            await _close_step(
                plan_id,
                step,
                store.STEP_UNKNOWN,
                f"нет признаков хода {minutes} мин: задача остановлена, результат неизвестен" + tail,
                "step_unknown",
                operator,
                est_pair,
                actual,
            )
            return "нет признаков хода, результат неизвестен"
        await _hold_after_stall(
            plan_id, step, spec, cur, project_id, reserved, t0, operator, est_pair, stall.idle_s
        )
        await _close_step(
            plan_id,
            step,
            store.STEP_UNKNOWN,
            f"нет признаков хода {minutes} мин: платную задачу не останавливали, "
            "результат неизвестен; проект занят, пока задача не закончится "
            "(остановить её можно только вручную)",
            "step_unknown",
            operator,
            est_pair,
        )
        return "нет признаков хода, платная задача не остановлена, результат неизвестен"
    except _SlotWaitTooLong as waited:
        # Проект так и не взял воркер: ход не начинался, снимаем запуск. Если воркер брал
        # его раньше в этом шаге или успел взять, пока снимали, «не начинался» уже
        # неправда: исход неизвестен.
        taken = bool(cur.get("ever_taken")) or _generation_active(project_id)
        confirmed = await _abort_after_failure(
            plan_id, step, spec, cur, project_id, reserved, t0, operator, est_pair, force=True
        )
        taken = taken or _generation_active(project_id)
        actual = await _settle_if_done(plan_id, step, spec, reserved, t0, confirmed)
        hours = max(1, round(waited.wait_s / 3600))
        if taken:
            tail = "остановлен" if confirmed else "остановка не подтверждена, проект занят до конца шага"
            await _close_step(
                plan_id,
                step,
                store.STEP_UNKNOWN,
                f"не дождался свободного места в очереди за {hours} ч, но воркер "
                f"уже брал проект в этом шаге: шаг {tail}, результат неизвестен",
                "step_unknown",
                operator,
                est_pair,
                actual,
            )
            return "не дождался свободного места, результат неизвестен"
        if confirmed:
            await _close_step(
                plan_id,
                step,
                store.STEP_FAILED,
                f"не дождался свободного места в очереди за {hours} ч: запуск снят, шаг не начинался",
                "step_failed",
                operator,
                est_pair,
                actual,
            )
            return f"не дождался свободного места за {hours} ч"
        await _close_step(
            plan_id,
            step,
            store.STEP_UNKNOWN,
            f"не дождался свободного места в очереди за {hours} ч; снятие запуска "
            "не подтверждено, результат неизвестен",
            "step_unknown",
            operator,
            est_pair,
            actual,
        )
        return "не дождался свободного места, результат неизвестен"
    except _Stopped:
        actual = await _settle_step(plan_id, step, spec, reserved, t0, None)
        await _close_step(
            plan_id,
            step,
            store.STEP_CANCELLED,
            "остановлено оператором",
            "step_cancelled",
            operator,
            est_pair,
            actual,
        )
        return "stopped"
    except _KillSwitchStopped:
        confirmed = await _abort_after_failure(
            plan_id, step, spec, cur, project_id, reserved, t0, operator, est_pair, force=False
        )
        actual = await _settle_if_done(plan_id, step, spec, reserved, t0, confirmed)
        await _close_step(
            plan_id,
            step,
            store.STEP_CANCELLED,
            "остановлено аварийным стопом",
            "step_cancelled",
            operator,
            est_pair,
            actual,
        )
        return "blocked"
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        confirmed = True
        if spec.is_job and cur.get("start") is not None and not cur.get("job_finished"):
            confirmed = await _abort_after_failure(
                plan_id, step, spec, cur, project_id, reserved, t0, operator, est_pair, force=False
            )
        actual = await _settle_if_done(plan_id, step, spec, reserved, t0, confirmed)
        # Итог задачи уже написан по-русски: имя класса исключения в чате лишнее.
        text = str(exc) if isinstance(exc, JobFailed) else f"{type(exc).__name__}: {exc}"
        await _close_step(plan_id, step, store.STEP_FAILED, text, "step_failed", operator, est_pair, actual)
        return text[:300]
    finally:
        _CURRENT.pop(plan_id, None)

    actual = await _settle_step(plan_id, step, spec, reserved, t0, credits)
    async with store.sessions()() as s:
        await s.execute(
            update(store.ActionPlanStep)
            .where(store.ActionPlanStep.id == step.id)
            .values(
                status=store.STEP_SUCCEEDED,
                result=safe_json(result),
                progress=1.0,
                cost_actual=actual,
                finished_at=_now(),
                error="",
            )
        )
        store.append_history(
            s,
            event="step_succeeded",
            plan_id=plan_id,
            step_index=step.idx,
            actor=operator,
            action_id=step.action_id,
            params=step.params,
            outcome="succeeded",
            cost_estimate=est_pair,
            cost_recorded=actual,
        )
        await s.commit()
    return "ok"


async def mark_stale_reserves() -> list[dict]:
    from app.settings import settings

    try:
        async with store.sessions()() as s:
            rows = await budget.unresolved_reserves(
                s,
                older_than_s=float(settings.action_layer_reserve_stale_s),
                skip_plans={info["plan_id"] for info in _ORPHANS.values()},
            )
            fresh = await budget.mark_unresolved_reserves(s, rows)
            await s.commit()
        return fresh
    except Exception:
        logger.exception("action layer stale reserve sweep failed")
        return []


async def _plan_project(plan_id: str) -> int | None:
    async with store.sessions()() as s:
        return await budget._plan_project(s, plan_id)


async def _settle_if_done(plan_id, step, spec, reserved, t0, confirmed: bool) -> float | None:
    if not confirmed:
        return None
    return await _settle_step(plan_id, step, spec, reserved, t0, None)


async def _abort_after_failure(
    plan_id, step, spec, cur, project_id, reserved, t0, operator, est_pair, *, force: bool
) -> bool:
    if not spec.is_job:
        return True
    if not (force or cur.get("start") is not None):
        return True
    try:
        confirmed = await _abort_job(plan_id, spec, cur)
    except Exception:
        logger.exception("action layer abort failed plan={}", plan_id)
        confirmed = False
    async with store.sessions()() as s:
        store.append_history(
            s,
            event="job_stop_after_failure",
            plan_id=plan_id,
            step_index=step.idx,
            actor=operator,
            action_id=spec.id,
            outcome="confirmed" if confirmed else "unconfirmed",
            error="" if confirmed else "серверная задача могла продолжиться",
            cost_estimate=est_pair,
        )
        await s.commit()
    if not confirmed:

        async def _late_settle(_state) -> None:
            await _settle_step(plan_id, step, spec, reserved, t0, None)

        _hold_project(project_id, plan_id, spec, cur, _late_settle)
    return confirmed


async def _hold_after_stall(
    plan_id, step, spec, cur, project_id, reserved, t0, operator, est_pair, idle_s: float
) -> None:
    async with store.sessions()() as s:
        store.append_history(
            s,
            event="job_stalled",
            plan_id=plan_id,
            step_index=step.idx,
            actor=operator,
            action_id=spec.id,
            outcome="unconfirmed",
            error=(
                f"нет признаков хода {idle_s:.0f} с; платная задача не останавливалась и могла продолжиться"
            ),
            cost_estimate=est_pair,
        )
        await s.commit()

    async def _late_settle(_state) -> None:
        await _settle_step(plan_id, step, spec, reserved, t0, None)

    _hold_project(project_id, plan_id, spec, cur, _late_settle)


def _credits(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


async def _call_stop(plan_id: str, cur: dict) -> None:
    if cur["stop_sent"] or cur["spec"].stop is None:
        return
    cur["stop_sent"] = True
    try:
        await cur["spec"].stop(cur["ctx"], cur["params"])
    except Exception as exc:
        logger.warning("action layer stop call failed plan={}: {}", plan_id, exc)


async def _execute(
    plan_id: str, spec: ActionSpec, ctx: ActionContext, params: dict, cur: dict
) -> tuple[dict, float | None]:
    from app.settings import settings

    start = await asyncio.wait_for(
        spec.executor(ctx, params), timeout=float(settings.action_layer_step_timeout_s)
    )
    start = start if isinstance(start, dict) else {"value": start}
    cur["start"] = start
    if not spec.is_job:
        return start, _credits(start.get("credits_spent"))
    watch = ProgressWatch(cur.get("project_id"))
    queued_s = 0.0
    queued_since: float | None = None
    try:
        while True:
            state = await spec.poll(ctx, params, start)
            slot = None if state.done else await _slot_wait(ctx, spec, cur)
            now = time.monotonic()
            # Воркер уже брал проект в этом шаге (идёт сейчас или вышел из очереди после
            # запуска): потом проект мог снова встать в очередь (повтор после ошибки,
            # автоцепочка), но «шаг не начинался» про него уже неправда.
            if _generation_active(cur.get("project_id")) or (
                queued_since is not None and slot is None and not state.done
            ):
                cur["ever_taken"] = True
            if slot is not None:
                if queued_since is None:
                    queued_since = now
                    _note_slot_waiter(plan_id, now)
                cur["slot_wait"] = {"busy": slot[0], "max_parallel": slot[1], "reason": slot[2]}
                await ctx.progress(state.progress, slot_wait_text(plan_id, slot[1], slot[2]))
            else:
                if queued_since is not None:
                    queued_s += now - queued_since
                    queued_since = None
                    _drop_slot_waiter(plan_id)
                    cur.pop("slot_wait", None)
                if state.progress is not None or state.note:
                    await ctx.progress(state.progress, state.note)
            if state.done:
                cur["job_finished"] = True
                if state.ok:
                    result = {**start, **(state.result or {})}
                    if queued_s >= 1:
                        result["queue_wait_s"] = round(queued_s)
                    return result, _credits(state.credits_spent)
                if await _stop_requested(plan_id):
                    raise _Stopped()
                raise JobFailed(state.error or "фоновая задача завершилась ошибкой")
            await _job_guards(plan_id, spec, ctx, cur)
            if queued_since is not None:
                watch.hold()
                # Срок вышел - перепроверяем сейчас, без await до raise: воркер мог взять
                # проект после чтения очереди в начале круга.
                if (
                    now - queued_since > slot_wait_max_s()
                    and project_waits_for_slot(cur.get("project_id"), _probed(cur)) is not None
                ):
                    raise _SlotWaitTooLong(now - queued_since)
            else:
                await watch.observe(ctx, state)
                if watch.stalled():
                    raise _Stalled(watch.idle_s())
            await asyncio.sleep(spec.poll_interval_s)
    finally:
        _drop_slot_waiter(plan_id)


async def _job_guards(plan_id: str, spec: ActionSpec, ctx: ActionContext, cur: dict) -> None:
    if await _stop_requested(plan_id):
        await _call_stop(plan_id, cur)
    if await _kill_switch_on():
        await _call_stop(plan_id, cur)
        async with store.sessions()() as s:
            store.append_history(
                s,
                event="kill_switch_block",
                plan_id=plan_id,
                step_index=ctx.step_index,
                actor=ctx.operator,
                action_id=spec.id,
                outcome="blocked",
                error=budget.KILL_SWITCH_REASON,
            )
            await s.commit()
        raise _KillSwitchStopped()


async def request_stop(plan_id: str, operator: str) -> dict:
    async with store.sessions()() as s:
        plan = await s.get(store.ActionPlan, plan_id)
        if plan is None:
            raise LookupError("План не найден.")
        if plan.status in (store.PLAN_SUCCEEDED, store.PLAN_FAILED, store.PLAN_STOPPED):
            return {"status": plan.status, "stop_requested": plan.stop_requested}
        plan.stop_requested = True
        plan.updated_at = _now()
        store.append_history(s, event="stop_requested", plan_id=plan_id, actor=operator, outcome="requested")
        await s.commit()
    cur = _CURRENT.get(plan_id)
    if cur is not None:
        await _call_stop(plan_id, cur)
    if not is_live(plan_id):
        await _finish(plan_id, store.PLAN_STOPPED, reason="operator_stop")
        return {"status": store.PLAN_STOPPED, "stop_requested": True}
    return {"status": store.PLAN_RUNNING, "stop_requested": True}
