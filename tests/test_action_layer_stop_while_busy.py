"""«Останови проект 7», пока в #7 идёт платный запуск другого плана.

Один живой план на проект — правило слоя действий. Но стоп проекта — это не
второй запуск: он не должен получать «В этом проекте уже выполняется другой план»,
иначе чат не может остановить проект, который сам же запустил. И стоп не должен
забирать у живого плана его захват проекта: третий запуск по-прежнему отказ.
Действия — заглушки в памяти, провайдеров нет.
"""

from __future__ import annotations

pytest_plugins = ['tests.action_layer_support']

import asyncio

from app.services.action_layer import approval, executor, service
from tests.action_layer_support import fresh_registry, make_spec

OP = approval.OperatorIdentity("web_operator:abc123")
_SCHEMA = {"type": "object", "properties": {"project_id": {"type": "integer"}}, "additionalProperties": False}


async def _plan(env, reg, *steps):
    plan_id, preview = await service.create_plan(env.app, list(steps), operator=OP,
                                                 request_text="запрос", registry=reg)
    assert plan_id, preview
    return plan_id


async def test_stop_runs_while_another_plan_holds_the_project(env) -> None:  # noqa: F811
    gate = asyncio.Event()
    stopped: list[int] = []

    async def slow(ctx, params):
        await gate.wait()
        return {}

    async def stop(ctx, params):
        stopped.append(int(params["project_id"]))
        return {"stopped": True}

    reg = fresh_registry(
        make_spec("t.slow", slow, risk="local_safe", schema=_SCHEMA),
        make_spec("project.stop", stop, risk="local_safe", schema=_SCHEMA),
    )
    run = await _plan(env, reg, {"action_id": "t.slow", "params": {"project_id": 7}})
    await service.prepare_run(run, OP, registry=reg)
    executor.launch(env.app, run, OP.id, registry=reg)
    await asyncio.sleep(0.05)
    try:
        # Пока стоп не прошёл, второй обычный план в #7 — отказ, как и раньше.
        other = await _plan(env, reg, {"action_id": "t.slow", "params": {"project_id": 7}})
        try:
            await service.prepare_run(other, OP, registry=reg)
            raise AssertionError("second run on a busy project must be refused")
        except service.RunRefused as exc:
            assert exc.code == "project_busy"

        halt = await _plan(env, reg, {"action_id": "project.stop", "params": {"project_id": 7}})
        await service.prepare_run(halt, OP, registry=reg)
        executor.launch(env.app, halt, OP.id, registry=reg)
        await executor.wait(halt)
        assert stopped == [7]
        view = await service.plan_view(halt)
        assert [s["status"] for s in view["steps"]] == ["succeeded"]
        # Стоп не забрал захват проекта у живого плана: новый запуск в #7 всё ещё отказ.
        third = await _plan(env, reg, {"action_id": "t.slow", "params": {"project_id": 7}})
        try:
            await service.prepare_run(third, OP, registry=reg)
            raise AssertionError("stop must not release the live plan's claim")
        except service.RunRefused as exc:
            assert exc.code == "project_busy"
    finally:
        gate.set()
        await executor.wait(run)
