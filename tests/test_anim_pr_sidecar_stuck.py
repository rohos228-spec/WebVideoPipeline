"""Сайдкар останавливается, когда перестал продвигаться.

Живой прогон 2026-08-31: очередь шла 33 → 27 → 24 → 8 и встала — проходы
падали, ничего не сохраняя, а цикл повторялся каждые ~70 секунд, и каждый
проход это оплаченный вызов модели. Считать надо ЗАСТОЙ, а не отказы: простой
счётчик падений остановил бы сайдкар и там, где он ещё продвигался.
"""

from __future__ import annotations

import asyncio

import pytest

from app.services import anim_pr_sidecar as sc


async def _no_sleep(*_args, **_kwargs) -> None:
    """Заглушка вместо пауз цикла: подменять `asyncio.sleep` им же — рекурсия."""
    return None


class _FakeSession:
    def __init__(self, project, frames):
        self._project = project
        self._frames = frames

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def get(self, _model, _pid):
        return self._project

    async def execute(self, _stmt):
        frames = self._frames

        class _Res:
            def scalars(self):
                class _S:
                    def all(self_inner):
                        return frames

                return _S()

        return _Res()


@pytest.mark.asyncio
async def test_pending_counts_items_from_collector(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.db.SessionLocal", lambda: _FakeSession(object(), ["f1", "f2"]))
    monkeypatch.setattr(
        "app.services.animation_prompt_gpt.collect_batch_items",
        lambda _p, frames: list(frames),
    )
    assert await sc._pending_now(4) == 2


@pytest.mark.asyncio
async def test_pending_is_none_without_project(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.db.SessionLocal", lambda: _FakeSession(None, []))
    assert await sc._pending_now(4) is None


@pytest.mark.asyncio
async def test_pending_is_none_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """Диагностика не должна ронять цикл — молча отдаём None."""

    def boom():
        raise RuntimeError("база недоступна")

    monkeypatch.setattr("app.db.SessionLocal", boom)
    assert await sc._pending_now(4) is None


@pytest.mark.asyncio
async def test_loop_stops_after_stuck_rounds(monkeypatch: pytest.MonkeyPatch) -> None:
    """Очередь не движется — сайдкар сдаётся, а не жжёт вызовы бесконечно."""
    calls = {"n": 0}

    async def always_fails(_pid, **_kw):
        calls["n"] += 1
        raise RuntimeError("битый JSON apply-ops")

    async def stuck(_pid):
        return 8

    monkeypatch.setattr(sc, "drain_anim_pr_from_image_prompts", always_fails)
    monkeypatch.setattr(sc, "_pending_now", stuck)
    monkeypatch.setattr(asyncio, "sleep", _no_sleep)

    await asyncio.wait_for(sc._sidecar_loop(4), timeout=5)
    # Первому проходу не с чем сравнивать остаток очереди, поэтому он
    # засчитывается как «мог продвинуться»: стоп наступает на N+1.
    assert calls["n"] == sc._MAX_STUCK_ROUNDS + 1


@pytest.mark.asyncio
async def test_progress_resets_the_stuck_counter(monkeypatch: pytest.MonkeyPatch) -> None:
    """Падения вперемешку с прогрессом — работаем дальше, пока очередь идёт."""
    calls = {"n": 0}
    queue = [30, 24, 18, 12, 12, 12, 12]

    async def always_fails(_pid, **_kw):
        calls["n"] += 1
        raise RuntimeError("обрыв")

    async def moving(_pid):
        return queue[min(calls["n"] - 1, len(queue) - 1)]

    monkeypatch.setattr(sc, "drain_anim_pr_from_image_prompts", always_fails)
    monkeypatch.setattr(sc, "_pending_now", moving)
    monkeypatch.setattr(asyncio, "sleep", _no_sleep)

    await asyncio.wait_for(sc._sidecar_loop(4), timeout=5)
    # три первых прохода двигали очередь, стоп только на трёх подряд без движения
    assert calls["n"] > sc._MAX_STUCK_ROUNDS


# ── Ветки, которые в полном прогоне ловились только удачным таймингом ────


class _Result:
    def __init__(self, run):
        self._run = run

    def scalar_one_or_none(self):
        return self._run

    def scalars(self):
        run = self._run

        class _S:
            def all(self):
                return run or []

        return _S()


class _SyncSession:
    def __init__(self, run):
        self._run = run
        self.committed = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def execute(self, _stmt):
        return _Result(self._run)

    async def get(self, _model, _pid):
        return None

    async def commit(self):
        self.committed += 1


@pytest.mark.asyncio
async def test_sync_without_workflow_run_is_false() -> None:
    from types import SimpleNamespace

    session = _SyncSession(None)
    p = SimpleNamespace(id=4)
    assert await sc.sync_anim_pr_noderun_done(session, p) is False


@pytest.mark.asyncio
async def test_sync_skips_foreign_and_finished_nodes_and_survives_publish(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Чужие типы и done-узлы пропускаются; упавший publish — не повод откатить."""
    from types import SimpleNamespace

    from app.models import NodeRunStatus

    foreign = SimpleNamespace(node_type="images", node_key="i1", status=NodeRunStatus.running)
    finished = SimpleNamespace(node_type="animation_prompts", node_key="a0", status=NodeRunStatus.done)
    pending = SimpleNamespace(node_type="animation_prompts", node_key="a1", status=NodeRunStatus.running)
    run = SimpleNamespace(id=7, node_runs=[foreign, finished, pending])

    def _mark_done(nr, **_kw):
        nr.status = NodeRunStatus.done
        return True

    async def _publish_boom(*_a, **_kw):
        raise RuntimeError("шина лежит")

    monkeypatch.setattr("app.services.node_status_machine.sync_node_done_from_data", _mark_done)
    monkeypatch.setattr("app.services.event_bus.publish_node_event", _publish_boom)

    session = _SyncSession(run)
    p = SimpleNamespace(id=4)
    assert await sc.sync_anim_pr_noderun_done(session, p) is True
    assert foreign.status is NodeRunStatus.running
    assert pending.status is NodeRunStatus.done


class _DrainSession(_FakeSession):
    def __init__(self, project, frames):
        super().__init__(project, frames)
        self.committed = 0

    async def commit(self):
        self.committed += 1


@pytest.mark.asyncio
async def test_drain_without_project_is_a_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.db.SessionLocal", lambda: _DrainSession(None, []))
    assert await sc.drain_anim_pr_from_image_prompts(4) == {"batches": 0, "pending": 0}


@pytest.mark.asyncio
async def test_drain_empty_queue_syncs_noderun(monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    from app.models import ProjectStatus

    p = SimpleNamespace(id=4, status=ProjectStatus.generating_images)
    session = _DrainSession(p, [])
    synced = {"n": 0}

    async def _sync(_s, _p):
        synced["n"] += 1
        return True

    monkeypatch.setattr("app.db.SessionLocal", lambda: session)
    monkeypatch.setattr("app.services.animation_prompt_gpt.collect_batch_items", lambda _p, _f: [])
    monkeypatch.setattr(sc, "sync_anim_pr_noderun_done", _sync)

    assert await sc.drain_anim_pr_from_image_prompts(4) == {"batches": 0, "pending": 0}
    assert synced["n"] == 1 and session.committed == 1


@pytest.mark.asyncio
async def test_drain_fills_and_reports_leftover(monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    from app.models import ProjectStatus

    p = SimpleNamespace(id=4, status=ProjectStatus.generating_images)
    session = _DrainSession(p, ["f1", "f2"])
    queues = [["f1", "f2"], []]  # до прохода и после
    synced = {"n": 0}

    async def _sync(_s, _p):
        synced["n"] += 1
        return True

    async def _fill(_s, _p, *, finalize_status, max_batches):
        assert finalize_status is False
        return {"batches": 2}

    monkeypatch.setattr("app.db.SessionLocal", lambda: session)
    monkeypatch.setattr(
        "app.services.animation_prompt_gpt.collect_batch_items",
        lambda _p, _f: queues.pop(0),
    )
    monkeypatch.setattr("app.orchestrator.steps.make_animation_prompts.fill_animation_prompts", _fill)
    monkeypatch.setattr(sc, "sync_anim_pr_noderun_done", _sync)

    assert await sc.drain_anim_pr_from_image_prompts(4) == {"batches": 2, "pending": 0}
    assert synced["n"] == 1 and session.committed == 1


@pytest.mark.asyncio
async def test_loop_success_paths_until_queue_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """Штатный цикл: пачки → хвост под img → хвост без img → пустая очередь → стоп."""
    from types import SimpleNamespace

    from app.models import ProjectStatus

    script = [
        {"batches": 1, "pending": 5},  # поработали — короткая пауза
        {"batches": 0, "pending": 3},  # img ещё льёт, очередь не пуста
        {"batches": 0, "pending": 0},  # img льёт, очередь пуста — idle
        {"batches": 0, "pending": 2},  # img кончился, хвост очереди
        {"batches": 0, "pending": 0},  # пусто — стоп
    ]
    statuses = iter(
        [
            ProjectStatus.generating_images,
            ProjectStatus.generating_images,
            ProjectStatus.animation_prompts_ready,
            ProjectStatus.animation_prompts_ready,
        ]
    )
    p = SimpleNamespace(id=4, status=None)

    async def _drain(_pid, **_kw):
        return script.pop(0)

    class _LoopSession(_FakeSession):
        async def get(self, _model, _pid):
            p.status = next(statuses)
            return p

    async def _sync(_s, _p):
        return False

    monkeypatch.setattr(sc, "drain_anim_pr_from_image_prompts", _drain)
    monkeypatch.setattr(sc, "sync_anim_pr_noderun_done", _sync)
    monkeypatch.setattr("app.db.SessionLocal", lambda: _LoopSession(p, []))
    monkeypatch.setattr(asyncio, "sleep", _no_sleep)

    await asyncio.wait_for(sc._sidecar_loop(4), timeout=5)
    assert not script, "цикл обязан дойти до пустой очереди и остановиться"


@pytest.mark.asyncio
async def test_ensure_does_not_start_a_second_task(monkeypatch: pytest.MonkeyPatch) -> None:
    gate = asyncio.Event()

    async def _hold(_pid):
        await gate.wait()

    monkeypatch.setattr(sc, "_sidecar_loop", _hold)
    assert sc.ensure_anim_pr_sidecar(999) is True
    try:
        assert sc.ensure_anim_pr_sidecar(999) is False
    finally:
        gate.set()
        task = sc._TASKS.pop(999, None)
        if task is not None:
            await task
