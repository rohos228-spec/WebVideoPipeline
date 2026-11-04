"""Живая отправка уведомления чата (шина → WebSocket): участнику — без сумм, админу — как
есть, чужому арендатору — ничего. Падает, пока WebSocket шлёт событие шины как есть."""

from __future__ import annotations

import asyncio
import inspect

import pytest

from app.services import chat_notice
from app.services.event_bus import get_bus
from app.services.tenant import tenant_scope
from app.web.member_money import ws_event_for_viewer

A = "11111111-1111-1111-1111-111111111111"
B = "22222222-2222-2222-2222-222222222222"
TEXT = "Потрачено 48.20 USD из $50"


@pytest.fixture
def notices_db(tmp_path, monkeypatch):
    from app.settings import settings

    monkeypatch.setattr(settings, "sqlite_path", tmp_path / "notices-live.db")
    chat_notice.reset()


async def _published_under_a() -> dict:
    async with get_bus().subscribe("global") as queue:
        with tenant_scope(A):
            _status, made = await chat_notice.send(None, "warn", "Потолок", TEXT, kind="spend_cap_live")
        assert made is not None
        return await asyncio.wait_for(queue.get(), timeout=5)


def _viewer(monkeypatch, *, member: bool) -> None:
    import app.services.action_layer.plan as plan

    monkeypatch.setattr(plan, "member_view", lambda: member)


async def test_member_gets_live_notice_without_amounts(notices_db, monkeypatch) -> None:
    event = await _published_under_a()
    assert TEXT in str(event)  # шина несёт полный текст
    _viewer(monkeypatch, member=True)
    with tenant_scope(A):
        sent = ws_event_for_viewer(event)
    assert sent is not None and sent["type"] == "chat_notice"
    assert "48.20" not in str(sent) and "$50" not in str(sent)
    assert "tenant_id" not in sent


async def test_admin_gets_live_notice_as_is(notices_db, monkeypatch) -> None:
    event = await _published_under_a()
    _viewer(monkeypatch, member=False)
    with tenant_scope(A):
        sent = ws_event_for_viewer(event)
    assert sent is not None and TEXT in sent["notice"]["text"]


async def test_other_tenant_gets_nothing_from_the_global_channel(notices_db, monkeypatch) -> None:
    event = await _published_under_a()
    _viewer(monkeypatch, member=True)
    with tenant_scope(B):
        assert ws_event_for_viewer(event) is None


def test_other_bus_events_pass_unchanged() -> None:
    event = {"type": "node_progress", "cost_usd": 1.0}
    assert ws_event_for_viewer(event) is event


def test_websocket_endpoint_filters_through_the_viewer() -> None:
    from app.web import api

    assert "ws_event_for_viewer(evt)" in inspect.getsource(api.create_app)
