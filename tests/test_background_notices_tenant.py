"""Фоновые уведомления (сторож, инциденты диагностики) доходят до клиента, чей это проект.

Проходы наблюдателя шли без арендатора: уведомление о проекте клиента писалось строкой
владельца и живым событием с меткой «владелец» — WebSocket клиента его отбрасывал, а под
RLS проход вовсе не видел выполнений клиента. Падает без chat_notice.owner_scope и без
проходов наблюдателя по арендаторам.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.services import chat_notice
from app.services.event_bus import get_bus
from app.services.tenant import current_tenant, tenant_scope
from app.web.member_money import ws_event_for_viewer

A = "11111111-1111-1111-1111-111111111111"


@pytest.fixture
def notices_db(tmp_path, monkeypatch):
    from app.settings import settings

    monkeypatch.setattr(settings, "sqlite_path", tmp_path / "bg-notices.db")
    chat_notice.reset()


async def _next_notice(queue) -> dict:
    while True:
        event = await asyncio.wait_for(queue.get(), timeout=5)
        if event.get("type") == chat_notice.EVENT_TYPE:
            return event


async def test_incident_notice_goes_out_as_the_project_tenant(notices_db, monkeypatch) -> None:
    import app.services.action_layer.plan as plan

    row = SimpleNamespace(
        details={"title": "Нужно решение"},
        severity="error",
        type="watchdog_stalled",
        project_id=13,
        incident_id="inc-bg-1",
        execution_id="ex-1",
        tenant_id=A,
    )
    async with get_bus().subscribe("global") as queue:
        assert current_tenant() is None  # фоновый проход
        status = await chat_notice.incident(row, "Потрачено 48.20 USD, проект #13 ждёт решения")
        event = await _next_notice(queue)
    assert status == "written"
    assert event["tenant_id"] == A
    monkeypatch.setattr(plan, "member_view", lambda: True)
    with tenant_scope(A):
        sent = ws_event_for_viewer(event)
    assert sent is not None and "проект #13" in sent["notice"]["text"] and "48.20" not in str(sent)


async def test_watchdog_stall_notice_goes_out_as_the_project_tenant(notices_db) -> None:
    from app.services.watchdog import Watchdog

    created = [
        {
            "incident_id": "wd-1",
            "execution_id": "ex-2",
            "project_id": 21,
            "node_key": "n_images",
            "stall": {"kind": "idle"},
            "tenant_id": A,
        }
    ]
    async with get_bus().subscribe("global") as queue:
        await Watchdog(observer=None)._notify(created)
        event = await _next_notice(queue)
    assert event["tenant_id"] == A
    assert "зависла работа в проекте #21" in event["notice"]["text"]


async def test_observer_passes_serve_only_tenants_with_work_and_diagnosis_is_throttled(monkeypatch) -> None:
    """Наблюдатель: владелец и живые арендаторы с активным выполнением; диагностика не чаще
    DIAGNOSIS_INTERVAL. Арендатор без работы пропускается до поиска личности."""
    from app.services import orch_tenancy
    from app.services.observation import Observer

    B = "22222222-2222-2222-2222-222222222222"
    asked: list[bool] = []

    async def _tenants(*, active_only: bool = False):
        asked.append(active_only)
        return [None, A, B]

    monkeypatch.setattr(orch_tenancy, "tenants_to_serve", _tenants)
    identities: list[str] = []

    async def _identity(tenant):
        identities.append(tenant)
        return None

    monkeypatch.setattr(orch_tenancy, "_identity_for", _identity)
    seen: dict[str, list] = {"watchdog": [], "diagnosis": []}

    class _Wd:
        def due(self) -> bool:
            return True

        async def tick(self, *, force: bool = False):
            seen["watchdog"].append((current_tenant(), force))
            return []

    class _Dg:
        async def catch_up(self):
            seen["diagnosis"].append(current_tenant())

    observer = Observer.__new__(Observer)
    observer.watchdog = _Wd()
    observer.diagnoser = _Dg()

    async def _has_work() -> bool:
        return current_tenant() in (None, A)  # у B активных выполнений нет

    observer._has_active_execution = _has_work
    await observer._watchdog()
    await observer._diagnose()
    await observer._diagnose()  # сразу снова — внутри интервала, прохода нет
    assert seen["watchdog"] == [(None, True), (A, True)]
    assert seen["diagnosis"] == [None, A]
    assert identities == [A, A] and B not in identities
    assert asked and all(asked)
