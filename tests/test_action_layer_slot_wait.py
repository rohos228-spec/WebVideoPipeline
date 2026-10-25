"""Проектов больше, чем мест воркера: ждущие в очереди не становятся «результат неизвестен».

Всё на заглушках: воркер с окном 2 сам берёт проекты по местам, платных вызовов нет.
"""
from __future__ import annotations

import asyncio

import pytest

from app.services import step_cancel
from app.services.action_layer import approval, executor, service
from app.services.action_layer.spec import JobState
from tests.action_layer_support import env, fresh_registry, make_spec  # noqa: F401
from datetime import UTC

OP = approval.OperatorIdentity("web_operator:slots")
PROJ_SCHEMA = {"type": "object", "properties": {"project_id": {"type": "integer", "minimum": 1}},
               "additionalProperties": False}


class FakeWorker:
    """Как воркер Студии: не больше ``slots`` проектов идут одновременно, остальные ждут."""

    def __init__(self, slots: int, job_s: float) -> None:
        self.slots = slots
        self.job_s = job_s
        self.requested: list[int] = []
        self.started: dict[int, float] = {}
        self.tasks: dict[int, asyncio.Task] = {}
        self.peak = 0
        self.gate = asyncio.Event()  # пока закрыт, все места считаются занятыми
        self.gate.set()
        self._loop: asyncio.Task | None = None

    def start(self) -> None:
        self._loop = asyncio.create_task(self._run(), name="fake-worker")

    async def stop(self) -> None:
        if self._loop is not None:
            self._loop.cancel()
            with pytest.raises(asyncio.CancelledError):
                await self._loop
        for pid, task in self.tasks.items():
            task.cancel()
            step_cancel.unregister_advance_task(pid)

    async def _run(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            for pid in list(self.requested):
                if pid in self.tasks:
                    continue
                if not self.gate.is_set() or step_cancel.active_advance_count() >= self.slots:
                    step_cancel.note_worker_wait(pid, "parallel_slots_full")  # как runtime_wait
                    continue
                step_cancel.clear_worker_wait(pid)
                task = asyncio.create_task(asyncio.sleep(self.job_s), name=f"fake-advance-{pid}")
                self.tasks[pid] = task
                self.started[pid] = loop.time()
                step_cancel.register_advance_task(pid, task)
            self.peak = max(self.peak, step_cancel.active_advance_count())
            await asyncio.sleep(0.01)


@pytest.fixture
def slots2(monkeypatch):
    from app.services import gen_queue

    monkeypatch.setattr(gen_queue, "worker_max_parallel", lambda: 2)
    step_cancel._worker_waits.clear()
    yield
    step_cancel._advance_tasks.clear()
    step_cancel._worker_waits.clear()
    executor._SLOT_WAITERS.clear()


def _job_spec(worker: FakeWorker, notes: list[str]):
    async def start(ctx, params):
        worker.requested.append(params["project_id"])
        return {"started": True}

    async def poll(ctx, params, started):
        pid = params["project_id"]
        task = worker.tasks.get(pid)
        if task is None:
            return JobState(done=False)  # воркер ещё не взял: признаков хода нет
        if task.done():
            return JobState(done=True, ok=True, result={"text_ru": f"#{pid}: шаг завершён."})
        age = asyncio.get_running_loop().time() - worker.started[pid]
        return JobState(done=False, progress=min(0.99, age / worker.job_s), note=f"идёт {age:.2f}")

    async def stop(ctx, params):
        worker.requested.remove(params["project_id"])

    return make_spec("t.run", start, risk="paid", spends=True, cost=(0, 1), schema=PROJ_SCHEMA,
                     poll=poll, stop=stop, poll_interval_s=0.03, tags=("worker_queue",))


def _spy_notes(monkeypatch, notes: list[str]) -> None:
    real = executor.slot_wait_text

    def spy(plan_id, total, reason="parallel_slots_full"):
        text = real(plan_id, total, reason)
        notes.append(text)
        return text

    monkeypatch.setattr(executor, "slot_wait_text", spy)


async def _add_projects(env, n: int) -> list[int]:
    from app.models import Project, ProjectStatus

    async with env.factory() as session:
        rows = [Project(topic="slots", slug=f"slots-{i}", status=ProjectStatus.new) for i in range(n)]
        session.add_all(rows)
        await session.commit()
        return [row.id for row in rows]


async def _launch(env, reg, pid: int) -> str:
    plan_id, preview = await service.create_plan(
        env.app, [{"action_id": "t.run", "params": {"project_id": pid}}], operator=OP,
        request_text="slots", registry=reg)
    assert plan_id, preview
    await service.approve_plan(env.app, plan_id, OP, registry=reg)
    await service.prepare_run(plan_id, OP, registry=reg)
    executor.launch(env.app, plan_id, OP.id, registry=reg)
    return plan_id


async def test_ten_projects_two_slots_queued_ones_are_not_unknown(env, monkeypatch, slots2) -> None:
    from app.settings import settings

    # Сторож хода короче, чем ждут последние в очереди (4 круга по 1.5 с = 6 с), но с
    # запасом против пауз цикла событий под 10 исполнителями: идущий проект меняет заметку
    # каждые 30 мс, и ложный простой возможен, только если цикл встанет на 3 с.
    stall_s = 3.0
    monkeypatch.setattr(settings, "action_layer_no_progress_timeout_s", stall_s)
    notes: list[str] = []
    _spy_notes(monkeypatch, notes)

    async def no_probe(ctx, project_id):
        return None  # ход виден по заметкам заглушки, чтение проекта не нужно

    monkeypatch.setattr(executor, "project_activity", no_probe)
    worker = FakeWorker(slots=2, job_s=1.5)
    reg = fresh_registry(_job_spec(worker, notes))
    pids = await _add_projects(env, 10)
    # Все 10 встают в очередь сразу, независимо от того, сколько длится запуск планов:
    # последние ждут 4 круга по 1.5 с, это заведомо дольше сторожа хода.
    worker.gate.clear()
    worker.start()
    try:
        plans = [await _launch(env, reg, pid) for pid in pids]
        await asyncio.sleep(0.1)
        worker.gate.set()
        await asyncio.wait_for(asyncio.gather(*(executor.wait(p) for p in plans)), timeout=30)
    finally:
        await worker.stop()
    views = [await service.plan_view(p) for p in plans]
    errors = [str(v["steps"][0].get("error") or "") for v in views]
    assert not [e for e in errors if "неизвест" in e or "признаков хода" in e], errors
    assert [v["status"] for v in views] == ["succeeded"] * 10, errors
    assert worker.peak == 2
    assert any("в очереди, ждет свободного места (" in n for n in notes)
    most = max(int(n.split("(", 1)[1].split(" из ", 1)[1].split(")", 1)[0]) for n in notes)
    assert most >= 3, notes[:5]
    # Последние ждали дольше сторожа хода, и это время записано отдельно.
    waits = [v["steps"][0]["result"].get("queue_wait_s", 0) for v in views]
    assert max(waits) > stall_s, str(waits)
    assert executor._SLOT_WAITERS == {}


async def test_progress_watch_restarts_when_project_leaves_queue(env, monkeypatch, slots2) -> None:
    """Сторож хода жив после очереди: проект пошёл и замолчал - «нет признаков хода»."""
    from app.settings import settings

    monkeypatch.setattr(settings, "action_layer_no_progress_timeout_s", 0.3)
    holder = asyncio.create_task(asyncio.sleep(0.6), name="fake-advance-holder")
    step_cancel.register_advance_task(10_001, holder)
    step_cancel.register_advance_task(10_002, holder)
    pid = await env.add_project(frames=1)
    seen = {"started": False, "stopped": False}

    async def start(ctx, params):
        return {"started": True}

    async def poll(ctx, params, started):
        if seen["stopped"]:
            return JobState(done=True, ok=False, error="остановлено")
        if not holder.done():
            step_cancel.note_worker_wait(pid, "parallel_slots_full")
        elif not seen["started"]:
            seen["started"] = True
            step_cancel.clear_worker_wait(pid)
            step_cancel.register_advance_task(pid, asyncio.create_task(asyncio.sleep(5)))
        return JobState(done=False, note="идёт" if seen["started"] else "")

    async def stop(ctx, params):
        seen["stopped"] = True
        task = step_cancel._advance_tasks.get(pid)
        if task is not None:
            task.cancel()

    reg = fresh_registry(make_spec("t.run", start, risk="read", schema=PROJ_SCHEMA, poll=poll,
                                   stop=stop, poll_interval_s=0.03, tags=("worker_queue",)))
    plan_id, preview = await service.create_plan(
        env.app, [{"action_id": "t.run", "params": {"project_id": pid}}], operator=OP,
        request_text="slots", registry=reg)
    assert plan_id, preview
    await service.prepare_run(plan_id, OP, registry=reg)
    executor.launch(env.app, plan_id, OP.id, registry=reg)
    await asyncio.wait_for(executor.wait(plan_id), timeout=10)
    view = await service.plan_view(plan_id)
    assert seen["started"], "проект должен был дождаться места, а не упасть в очереди"
    assert "нет признаков хода" in str(view["steps"][0]["error"])
    task = step_cancel._advance_tasks.get(pid)
    if task is not None:
        task.cancel()


async def test_endless_queue_wait_is_cut_with_a_known_outcome(env, monkeypatch, slots2) -> None:
    monkeypatch.setenv("ACTION_LAYER_SLOT_WAIT_MAX_S", "0.2")
    monkeypatch.setenv("ACTION_LAYER_STOP_GRACE_S", "1")
    holder = asyncio.create_task(asyncio.sleep(30), name="fake-advance-holder")
    step_cancel.register_advance_task(10_001, holder)
    step_cancel.register_advance_task(10_002, holder)
    pid = await env.add_project(frames=1)
    step_cancel.note_worker_wait(pid, "parallel_slots_full")
    stopped = {"n": 0}

    async def start(ctx, params):
        return {"started": True}

    async def poll(ctx, params, started):
        if stopped["n"]:
            return JobState(done=True, ok=False, error="остановлено")
        return JobState(done=False)

    async def stop(ctx, params):
        stopped["n"] += 1

    reg = fresh_registry(make_spec("t.run", start, risk="read", schema=PROJ_SCHEMA, poll=poll,
                                   stop=stop, poll_interval_s=0.03, tags=("worker_queue",)))
    plan_id, preview = await service.create_plan(
        env.app, [{"action_id": "t.run", "params": {"project_id": pid}}], operator=OP,
        request_text="slots", registry=reg)
    assert plan_id, preview
    await service.prepare_run(plan_id, OP, registry=reg)
    executor.launch(env.app, plan_id, OP.id, registry=reg)
    try:
        await asyncio.wait_for(executor.wait(plan_id), timeout=10)
    finally:
        holder.cancel()
    view = await service.plan_view(plan_id)
    step = view["steps"][0]
    assert stopped["n"] == 1
    assert step["status"] == "failed", step
    assert "не дождался свободного места" in step["error"] and "неизвест" not in step["error"]


def test_slot_wait_text_counts_place_in_line() -> None:
    executor._SLOT_WAITERS.clear()
    try:
        for n, plan in enumerate(("a", "b", "c")):
            executor._SLOT_WAITERS[plan] = float(n)
        assert executor.slot_wait_text("b", 2).startswith("в очереди, ждет свободного места (2 из 3)")
        assert "не больше 2" in executor.slot_wait_text("a", 2)
    finally:
        executor._SLOT_WAITERS.clear()


def _running_status() -> str:
    from app.services.step_registry import running_statuses

    return sorted(s.value for s in running_statuses())[0]


def test_waits_for_slot_only_when_all_slots_are_busy(monkeypatch, slots2) -> None:
    monkeypatch.setattr(step_cancel, "is_generation_active", lambda pid: pid == 1)
    monkeypatch.setattr(step_cancel, "active_advance_count", lambda: 2)
    running = {"status": _running_status(), "meta": {}}
    step_cancel.note_worker_wait(1, "parallel_slots_full")
    assert executor.project_waits_for_slot(1) is None  # сам идёт
    step_cancel.note_worker_wait(2, "parallel_slots_full")
    assert executor.project_waits_for_slot(2) == (2, 2, "parallel_slots_full")
    assert executor.project_waits_for_slot(None) is None
    # Воркер ещё не смотрел проект: очередь только у running-проекта при занятых местах.
    assert executor.project_waits_for_slot(3) is None
    assert executor.project_waits_for_slot(3, running) == (2, 2, "parallel_slots_full")
    assert executor.project_waits_for_slot(3, {"status": "new", "meta": {}}) is None
    monkeypatch.setattr(step_cancel, "active_advance_count", lambda: 1)
    assert executor.project_waits_for_slot(3, running) is None  # место есть: обычный сторож хода


def test_worker_reason_decides_what_is_a_queue(monkeypatch, slots2) -> None:
    """Очередь - это места заняты или впереди очередь генерации; пауза и стоп - не очередь."""
    from datetime import datetime, timedelta, timezone

    monkeypatch.setattr(step_cancel, "is_generation_active", lambda pid: False)
    monkeypatch.setattr(step_cancel, "active_advance_count", lambda: 0)
    step_cancel.note_worker_wait(5, "queue_predecessor")
    assert executor.project_waits_for_slot(5) == (0, 2, "queue_predecessor")  # места есть, но очередь
    assert "впереди в очереди генерации" in executor.slot_wait_text("p", 2, "queue_predecessor")
    monkeypatch.setattr(step_cancel, "active_advance_count", lambda: 2)
    running = {"status": _running_status(), "meta": {}}
    for reason in ("failure_sleep", "user_stop"):
        step_cancel.note_worker_wait(6, reason)
        assert executor.project_waits_for_slot(6, running) is None, reason
    step_cancel.keep_worker_waits([5])
    assert step_cancel.worker_wait_reason(6) is None
    assert step_cancel.worker_wait_reason(5) == "queue_predecessor"
    later = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    for meta in ({"user_stop": True}, {"step_failure": {"sleep_until": later}}):
        assert executor.project_waits_for_slot(7, {"status": _running_status(), "meta": meta}) is None


def test_only_worker_steps_can_wait_in_queue() -> None:
    async def noop(ctx, params):
        return {}

    for action_id in ("step.run_step", "node.run_step", "project.resume", "project.continue_pipeline"):
        assert executor.goes_through_worker(make_spec(action_id, noop)), action_id
    assert executor.goes_through_worker(make_spec("x", noop, tags=("worker_queue",)))
    for action_id in ("montage.generate_scene_images", "montage.recover_outsee", "montage.render"):
        assert not executor.goes_through_worker(make_spec(action_id, noop)), action_id


async def _run_one(env, reg, action_id: str, pid: int) -> str:
    plan_id, preview = await service.create_plan(
        env.app, [{"action_id": action_id, "params": {"project_id": pid}}], operator=OP,
        request_text="slots", registry=reg)
    assert plan_id, preview
    await service.prepare_run(plan_id, OP, registry=reg)
    executor.launch(env.app, plan_id, OP.id, registry=reg)
    return plan_id


def _hold_two_slots(seconds: float) -> asyncio.Task:
    holder = asyncio.create_task(asyncio.sleep(seconds), name="fake-advance-holder")
    step_cancel.register_advance_task(10_001, holder)
    step_cancel.register_advance_task(10_002, holder)
    return holder


def _cancel_advance(pid: int) -> None:
    task = step_cancel._advance_tasks.get(pid)
    if task is not None:
        task.cancel()


async def _no_probe(ctx, project_id):
    return None


async def test_paid_job_outside_worker_is_not_taken_for_queued(env, monkeypatch, slots2) -> None:
    """Платная задача не через воркер при занятых местах: сторож хода жив, очереди нет."""
    from app.settings import settings

    monkeypatch.setattr(settings, "action_layer_no_progress_timeout_s", 0.3)
    monkeypatch.setenv("ACTION_LAYER_STOP_GRACE_S", "1")
    monkeypatch.setattr(executor, "project_activity", _no_probe)
    notes: list[str] = []
    _spy_notes(monkeypatch, notes)
    holder = _hold_two_slots(30)
    pid = await env.add_project(frames=1)
    stopped = {"n": 0}

    async def start(ctx, params):
        return {"started": True}

    async def poll(ctx, params, started):
        if stopped["n"]:
            return JobState(done=True, ok=False, error="остановлено")
        return JobState(done=False)  # повисла

    async def stop(ctx, params):
        stopped["n"] += 1

    reg = fresh_registry(make_spec("montage.generate_scene_images", start, risk="read",
                                   schema=PROJ_SCHEMA, poll=poll, stop=stop, poll_interval_s=0.03))
    plan_id = await _run_one(env, reg, "montage.generate_scene_images", pid)
    try:
        await asyncio.wait_for(executor.wait(plan_id), timeout=10)
    finally:
        holder.cancel()
    error = str((await service.plan_view(plan_id))["steps"][0]["error"])
    assert "нет признаков хода" in error and "не начинался" not in error, error
    assert notes == [] and executor._SLOT_WAITERS == {}


async def test_queue_predecessor_with_free_slots_is_queue_not_unknown(env, monkeypatch, slots2) -> None:
    """Места свободны, но в очереди генерации впереди проекты: это очередь, а не простой."""
    from app.settings import settings

    monkeypatch.setattr(settings, "action_layer_no_progress_timeout_s", 0.3)
    monkeypatch.setattr(executor, "project_activity", _no_probe)
    notes: list[str] = []
    _spy_notes(monkeypatch, notes)
    pid = await env.add_project(frames=1)
    step_cancel.note_worker_wait(pid, "queue_predecessor")
    loop = asyncio.get_running_loop()
    first: list[float] = []

    async def start(ctx, params):
        return {"started": True}

    async def poll(ctx, params, started):
        if not first:
            first.append(loop.time())
        age = loop.time() - first[0]
        if age < 1.2:
            return JobState(done=False)  # ждёт проекты впереди
        step_cancel.clear_worker_wait(pid)
        if age < 1.5:
            return JobState(done=False, note=f"идёт {age:.2f}")
        return JobState(done=True, ok=True, result={"text_ru": "готово"})

    reg = fresh_registry(make_spec("node.run_step", start, risk="read", schema=PROJ_SCHEMA,
                                   poll=poll, poll_interval_s=0.03))
    plan_id = await _run_one(env, reg, "node.run_step", pid)
    await asyncio.wait_for(executor.wait(plan_id), timeout=10)
    view = await service.plan_view(plan_id)
    assert view["status"] == "succeeded", view["steps"][0]
    assert any("впереди в очереди генерации" in n for n in notes)
    assert view["steps"][0]["result"].get("queue_wait_s", 0) >= 1


async def test_fallback_reads_running_status_before_worker_looks(env, monkeypatch, slots2) -> None:
    """Воркер занят своим шагом и проект ещё не видел: очередь по running-статусу проекта."""
    from app.models import Project, ProjectStatus
    from app.settings import settings

    monkeypatch.setattr(settings, "action_layer_no_progress_timeout_s", 0.3)
    monkeypatch.setattr(settings, "action_layer_progress_probe_s", 0.05)
    monkeypatch.setattr(executor, "project_activity", _no_probe)
    notes: list[str] = []
    _spy_notes(monkeypatch, notes)
    holder = _hold_two_slots(0.8)
    pid = await env.add_project(frames=1)
    async with env.factory() as session:
        row = await session.get(Project, pid)
        row.status = ProjectStatus(_running_status())
        await session.commit()
    seen = {"started": 0.0}
    loop = asyncio.get_running_loop()

    async def start(ctx, params):
        return {"started": True}

    async def poll(ctx, params, started):
        if not holder.done():
            return JobState(done=False)
        if not seen["started"]:
            seen["started"] = loop.time()
            step_cancel.register_advance_task(pid, asyncio.create_task(asyncio.sleep(5)))
        if loop.time() - seen["started"] < 0.2:
            return JobState(done=False, note="идёт")
        return JobState(done=True, ok=True, result={"text_ru": "готово"})

    reg = fresh_registry(make_spec("step.run_step", start, risk="read", schema=PROJ_SCHEMA,
                                   poll=poll, poll_interval_s=0.03))
    plan_id = await _run_one(env, reg, "step.run_step", pid)
    try:
        await asyncio.wait_for(executor.wait(plan_id), timeout=10)
    finally:
        _cancel_advance(pid)
    view = await service.plan_view(plan_id)
    assert view["status"] == "succeeded", view["steps"][0]
    assert any("в очереди, ждет свободного места" in n for n in notes)


async def test_worker_takes_project_during_cut_off_is_not_never_started(env, monkeypatch,
                                                                       slots2) -> None:
    """Воркер взял проект, пока шаг снимали за долгую очередь: не «не начинался», а неизвестно."""
    monkeypatch.setenv("ACTION_LAYER_SLOT_WAIT_MAX_S", "0.2")
    monkeypatch.setenv("ACTION_LAYER_STOP_GRACE_S", "1")
    holder = _hold_two_slots(30)
    pid = await env.add_project(frames=1)
    step_cancel.note_worker_wait(pid, "parallel_slots_full")
    stopped = {"n": 0}

    async def start(ctx, params):
        return {"started": True}

    async def poll(ctx, params, started):
        if stopped["n"]:
            return JobState(done=True, ok=False, error="остановлено")
        return JobState(done=False)

    async def stop(ctx, params):
        stopped["n"] += 1
        # гонка: воркер как раз взял проект
        step_cancel.register_advance_task(pid, asyncio.create_task(asyncio.sleep(5)))

    reg = fresh_registry(make_spec("t.run", start, risk="read", schema=PROJ_SCHEMA, poll=poll,
                                   stop=stop, poll_interval_s=0.03, tags=("worker_queue",)))
    plan_id = await _run_one(env, reg, "t.run", pid)
    try:
        await asyncio.wait_for(executor.wait(plan_id), timeout=10)
    finally:
        holder.cancel()
        _cancel_advance(pid)
    step = (await service.plan_view(plan_id))["steps"][0]
    assert "не начинался" not in step["error"] and "результат неизвестен" in step["error"], step


async def test_requeue_after_worker_took_project_is_unknown_not_never_started(
        env, monkeypatch, slots2) -> None:
    """Воркер уже брал проект в этом шаге (потом повтор после ошибки снова встал в очередь):
    долгая очередь закрывает шаг «результат неизвестен», а не «шаг не начинался»."""
    from app.settings import settings

    monkeypatch.setenv("ACTION_LAYER_SLOT_WAIT_MAX_S", "0.3")
    monkeypatch.setenv("ACTION_LAYER_STOP_GRACE_S", "1")
    monkeypatch.setattr(settings, "action_layer_no_progress_timeout_s", 5.0)
    monkeypatch.setattr(executor, "project_activity", _no_probe)
    pid = await env.add_project(frames=1)
    state = {"polls": 0, "holder": None}
    stopped = {"n": 0}

    async def start(ctx, params):
        return {"started": True}

    async def poll(ctx, params, started):
        if stopped["n"]:
            return JobState(done=True, ok=False, error="остановлено")
        state["polls"] += 1
        n = state["polls"]
        if n == 1:
            # Сначала проект ждёт места.
            step_cancel.note_worker_wait(pid, "parallel_slots_full")
            step_cancel.register_advance_task(10_001, asyncio.create_task(asyncio.sleep(30)))
            step_cancel.register_advance_task(10_002, asyncio.create_task(asyncio.sleep(30)))
            return JobState(done=False)
        if n == 3:
            # Воркер взял проект: места освободились, проект идёт.
            for other in (10_001, 10_002):
                step_cancel._advance_tasks.pop(other).cancel()
            step_cancel.clear_worker_wait(pid)
            step_cancel.register_advance_task(pid, asyncio.create_task(asyncio.sleep(0.1)))
        if 3 <= n < 6:
            return JobState(done=False, note=f"идёт {n}")
        if n == 6:
            # Шаг упал внутри, воркер повторяет его, но места снова заняты: опять очередь.
            _cancel_advance(pid)
            step_cancel.unregister_advance_task(pid)
            state["holder"] = _hold_two_slots(30)
            step_cancel.note_worker_wait(pid, "parallel_slots_full")
        return JobState(done=False)

    async def stop(ctx, params):
        stopped["n"] += 1

    reg = fresh_registry(make_spec("t.run", start, risk="read", schema=PROJ_SCHEMA, poll=poll,
                                   stop=stop, poll_interval_s=0.03, tags=("worker_queue",)))
    plan_id = await _run_one(env, reg, "t.run", pid)
    try:
        await asyncio.wait_for(executor.wait(plan_id), timeout=10)
    finally:
        if state["holder"] is not None:
            state["holder"].cancel()
        for other in (10_001, 10_002):
            task = step_cancel._advance_tasks.get(other)
            if task is not None:
                task.cancel()
        _cancel_advance(pid)
    step = (await service.plan_view(plan_id))["steps"][0]
    assert state["polls"] > 6, state
    assert stopped["n"] == 1
    assert step["status"] == "unknown", step
    assert "не начинался" not in step["error"] and "результат неизвестен" in step["error"], step


async def test_cut_off_rechecks_queue_before_stopping(env, monkeypatch, slots2) -> None:
    """Срок очереди вышел, но воркер взял проект в этом же круге: шаг не снимают."""
    monkeypatch.setenv("ACTION_LAYER_SLOT_WAIT_MAX_S", "0.001")
    holder = _hold_two_slots(30)
    pid = await env.add_project(frames=1)
    step_cancel.note_worker_wait(pid, "parallel_slots_full")
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    real_guards = executor._job_guards
    stopped = {"n": 0}
    calls = {"n": 0}

    async def guards(plan_id, spec, ctx, cur):
        await real_guards(plan_id, spec, ctx, cur)
        calls["n"] += 1
        # Второй круг: срок очереди уже вышел, а воркер взял проект между чтением и сроком.
        if calls["n"] == 2:
            step_cancel.clear_worker_wait(pid)  # воркер взял проект после чтения очереди
            step_cancel.register_advance_task(pid, asyncio.create_task(asyncio.sleep(5)))

    monkeypatch.setattr(executor, "_job_guards", guards)

    async def start(ctx, params):
        return {"started": True}

    async def poll(ctx, params, started):
        if loop.time() - t0 > 0.5:
            return JobState(done=True, ok=True, result={"text_ru": "готово"})
        return JobState(done=False, note="идёт" if pid in step_cancel._advance_tasks else "")

    async def stop(ctx, params):
        stopped["n"] += 1

    reg = fresh_registry(make_spec("t.run", start, risk="read", schema=PROJ_SCHEMA, poll=poll,
                                   stop=stop, poll_interval_s=0.03, tags=("worker_queue",)))
    plan_id = await _run_one(env, reg, "t.run", pid)
    try:
        await asyncio.wait_for(executor.wait(plan_id), timeout=10)
    finally:
        holder.cancel()
        _cancel_advance(pid)
    view = await service.plan_view(plan_id)
    assert view["status"] == "succeeded", view["steps"][0]
    assert stopped["n"] == 0


@pytest.mark.parametrize(("count", "busy", "total", "want"), [
    (10, 0, 2, "Одновременно идут не больше 2 проектов: 2 начнут сразу, 8 встанут в очередь"),
    (5, 0, 10, "Одновременно идут не больше 10 проектов: все 5 начнут сразу."),
    (3, 1, 1, "Одновременно идут не больше 1 проекта, сейчас уже занято 1 из 1: все 3 встанут в очередь"),
])
def test_slots_text_tells_how_many_run_and_wait(count, busy, total, want) -> None:
    from app.orchestrator.agents import approval_steps

    assert approval_steps.slots_text(approval_steps.slots_plan(count, busy, total)).startswith(want)


def test_parallel_group_card_shows_slots(monkeypatch) -> None:
    pytest.importorskip("app.orchestrator.actions")
    from app.orchestrator.agents import approval_steps
    from app.orchestrator.contracts import PlanStep
    from tests.agents.mocks import MockActions

    monkeypatch.setattr(approval_steps, "_worker_slots", lambda: (0, 2))
    steps = [PlanStep(action="step.run_step", params={"project_id": n, "step_code": "images"}, paid=True)
             for n in range(1, 11)]
    views = approval_steps.plan_steps(steps, MockActions())
    prev = {"cost": {"known": True}, "cost_estimate": 0.5}
    card = approval_steps.group_card(steps, list(range(10)), views, [prev] * 10, "fp", concurrent=True)
    assert card["slots"] == {"count": 10, "max_parallel": 2, "busy": 0, "start_now": 2, "waiting": 8}
    assert card["warnings"][0].startswith("Одновременно идут не больше 2 проектов: 2 начнут сразу, 8 ")
    plain = approval_steps.group_card(steps, list(range(10)), views, [prev] * 10, "fp")
    assert "slots" not in plain


def test_final_report_lists_every_project_outcome() -> None:
    from app.orchestrator.agents import summarizer as ts

    notes = [ts.StepNote("step.run_step", "Запустить шаг", ok=True, readonly=False, known=True,
                         text=f"#{pid}: шаг завершён.", project_id=pid) for pid in range(1, 9)]
    notes.append(ts.StepNote("step.run_step", "Запустить шаг", ok=False, readonly=False,
                             error="шаг не выполнен: кончились кредиты", project_id=9))
    notes.append(ts.StepNote("step.run_step", "Запустить шаг", ok=False, readonly=False,
                             error="результат неизвестен: связь пропала", project_id=10))
    text = ts.deterministic_text(ts.TaskFacts(goal="запусти 10", status="done", steps=notes))
    assert "• #1: шаг завершён." in text and "• #8: шаг завершён." in text
    assert "Не получилось «Запустить шаг» в проекте #9: шаг не выполнен" in text
    assert "Итог по проектам: готово 8 из 10; не получилось: #9; результат неизвестен: #10." in text
    one = ts.deterministic_text(ts.TaskFacts(goal="x", status="done", steps=notes[:1]))
    assert "Итог по проектам" not in one


async def test_worker_marks_rest_of_tick_with_their_own_reason(monkeypatch, slots2) -> None:
    """Места кончились на одном проекте: за ним предшественник в очереди генерации - не
    «места заняты», а «queue_predecessor»; стоп, пауза и идущий - своя причина."""
    from types import SimpleNamespace

    from app import main
    from app.services import gen_queue, gen_queue_run, step_failure_policy

    rows = [SimpleNamespace(id=n) for n in (21, 22, 23, 24, 25)]

    async def blocks(session, pid):
        return 1 if pid == 22 else None

    monkeypatch.setattr(gen_queue, "gen_queue_blocks_project", blocks)
    monkeypatch.setattr(gen_queue_run, "is_user_stopped", lambda p: p.id == 23)
    monkeypatch.setattr(step_failure_policy, "is_sleeping", lambda p: p.id == 24)
    monkeypatch.setattr(step_cancel, "is_generation_active", lambda pid: pid == 25)
    step_cancel.note_worker_wait(25, "parallel_slots_full")
    await main._note_rest_worker_waits(object(), rows)
    assert {n: step_cancel.worker_wait_reason(n) for n in (21, 22, 23, 24, 25)} == {
        21: "parallel_slots_full", 22: "queue_predecessor", 23: "user_stop",
        24: "failure_sleep", 25: None}


_TA = "00000000-0000-4000-8000-00000000000a"
_TB = "00000000-0000-4000-8000-00000000000b"


def test_worker_waits_pruned_per_tenant() -> None:
    """Проход воркера идёт от имени одного арендатора: чистка тика не стирает чужие причины."""
    from app.services import step_cancel as sc
    from app.services.tenant import tenant_scope

    sc._worker_waits.clear()
    try:
        with tenant_scope(_TA):
            sc.note_worker_wait(101, "parallel_slots_full")
        with tenant_scope(_TB):
            sc.note_worker_wait(202, "queue_predecessor")
            # Тик арендатора B видит только свои running-проекты (тут — ни одного).
            sc.keep_worker_waits([])
        assert sc.worker_wait_reason(101) == "parallel_slots_full"
        assert sc.worker_wait_reason(202) is None
        with tenant_scope(_TA):
            sc.keep_worker_waits([101])
        assert sc.worker_wait_reason(101) == "parallel_slots_full"
    finally:
        sc._worker_waits.clear()
