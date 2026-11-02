"""Автоповтор после сбоя: проект ещё держит план из чата — подождать, а не отдать человеку."""

from __future__ import annotations

import asyncio

import pytest

from app.services import orchestrator_autonomy_run as run
from app.services.action_layer import executor, service
from app.services.action_layer import node_failure as nf


def test_connect_error_is_provider_failure() -> None:
    err = "GPT: пустой ответ: GPT(chat/stream) пустой output после ConnectError: All connection attempts failed"
    assert nf.classify(errors=[err], rejects=[], frames=3, is_check=False)["code"] == "provider_error"


@pytest.mark.asyncio
async def test_launch_waits_while_chat_plan_holds_project(monkeypatch) -> None:
    calls = {"prepare": 0}

    async def approve_plan(app, plan_id, who):
        return None

    async def prepare_run(plan_id, who):
        calls["prepare"] += 1
        if calls["prepare"] < 3:
            raise service.RunRefused("project_busy", "В этом проекте уже запускается другой план.")

    async def done():
        return None

    async def plan_view(plan_id):
        return {"steps": [{"status": "succeeded", "result": {"text_ru": "ok"}, "cost": {"actual": 0}}]}

    monkeypatch.setattr(service, "approve_plan", approve_plan)
    monkeypatch.setattr(service, "prepare_run", prepare_run)
    monkeypatch.setattr(service, "plan_view", plan_view)
    monkeypatch.setattr(executor, "launch", lambda app, plan_id, who: asyncio.ensure_future(done()))
    monkeypatch.setattr(run, "_BUSY_POLL_S", 0.01)
    res = await run._launch(None, "p1", "local_web_operator", approve=True)
    assert res["ok"] and res["started"]
    assert calls["prepare"] == 3


@pytest.mark.asyncio
async def test_launch_gives_up_after_wait_and_other_refusals_are_immediate(monkeypatch) -> None:
    calls = {"prepare": 0}

    async def prepare_busy(plan_id, who):
        calls["prepare"] += 1
        raise service.RunRefused("project_busy", "занят")

    monkeypatch.setattr(service, "prepare_run", prepare_busy)
    monkeypatch.setattr(run, "_BUSY_POLL_S", 0.01)
    monkeypatch.setattr(run, "_BUSY_WAIT_S", 0.03)
    res = await run._launch(None, "p1", "op", approve=False)
    assert not res["started"] and res["error"] == "занят"
    assert calls["prepare"] >= 2

    calls["prepare"] = 0

    async def prepare_kill(plan_id, who):
        calls["prepare"] += 1
        raise service.RunRefused("kill_switch_active", "стоп")

    monkeypatch.setattr(service, "prepare_run", prepare_kill)
    res = await run._launch(None, "p1", "op", approve=False)
    assert res["error"] == "стоп" and calls["prepare"] == 1
