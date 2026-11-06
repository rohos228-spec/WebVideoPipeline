"""POST /sidebar-layout/gen-queue/enqueue must be registered on router."""

from __future__ import annotations

from app.web.routers.sidebar_layout import router


def test_gen_queue_enqueue_route_registered() -> None:
    paths = [r.path for r in router.routes]
    assert "/sidebar-layout/gen-queue/enqueue" in paths



async def test_enqueue_clears_auto_await_manual_start(monkeypatch):
    """После рестарта startup_guard ставит «ждать ручной ▶»; постановка в очередь это ▶."""
    from contextlib import asynccontextmanager
    from types import SimpleNamespace

    from app.models import ProjectStatus
    from app.services.project_control import arm_auto_await_manual_start, auto_awaits_manual_start
    from app.web.routers import sidebar_layout as mod

    project = SimpleNamespace(id=5, auto_mode=True, status=ProjectStatus.frames_ready, meta={})
    monkeypatch.setattr("app.services.project_control.flag_modified", lambda *a, **k: None)
    arm_auto_await_manual_start(project)
    assert auto_awaits_manual_start(project)

    class _Res:
        def scalar_one_or_none(self):
            return project

    class _Session:
        async def execute(self, *_):
            return _Res()

        async def commit(self):
            pass

    @asynccontextmanager
    async def _scope():
        yield _Session()

    async def _run(session, p, **kw):
        p.meta = {**p.meta, "gen_queue_run": {"mode": kw["mode"]}}

    async def _own(_):
        return None

    monkeypatch.setattr(mod, "session_scope", _scope)
    monkeypatch.setattr(mod, "set_gen_queue_run", _run)
    monkeypatch.setattr(mod, "_own_ids", _own)
    monkeypatch.setattr(mod.layout_svc, "get_gen_queue", lambda: [5])
    monkeypatch.setattr(mod.layout_svc, "clear_gen_queue_halted", lambda **k: None)
    monkeypatch.setattr(mod.layout_svc, "own_queue_view", lambda q, own: {"gen_queue_positions": {5: 1}})
    await mod.enqueue_gen_queue(mod.GenQueueEnqueue(project_id=5))
    assert not auto_awaits_manual_start(project)
