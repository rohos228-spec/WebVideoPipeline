"""Ручки чата для веб-интерфейса: отказ по карточке и стоп задачи — в сессии арендатора;
участник не получает сумм в $ ни в событиях SSE, ни в снимке статуса."""

from __future__ import annotations

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.orchestrator.contracts import OrchEvent, TaskStatus
from app.services.orch_tenancy import scoped_session
from app.services.studio_auth import set_identity


def test_member_safe_drops_money_fields_and_masks_amounts() -> None:
    from app.web.routers.orchestrator_chat import MEMBER_COST_TEXT, member_safe

    card = {
        "title": "Картинки",
        "cost_estimate": 0.42,
        "cost": {"known": True, "high_usd": 0.42},
        "cost_text": "около $0.42",
        "steps": [{"title": "Картинки", "estimated_cost": 0.42, "paid": True}],
        "text": "Готово: картинки. Потрачено $0.42.",
    }
    safe = member_safe(card, chat=True)
    assert "cost_estimate" not in safe and safe["cost"] == {"known": True}
    assert safe["cost_text"] == MEMBER_COST_TEXT
    assert safe["steps"] == [{"title": "Картинки", "paid": True}]
    assert "$" not in safe["text"] and "Готово: картинки." in safe["text"]


def test_sse_hides_money_for_member_only(monkeypatch) -> None:
    import app.services.action_layer.plan as plan
    from app.web.routers.orchestrator_chat import _sse

    event = OrchEvent(type="approval_required", seq=1, session_id="main", payload={"cost_estimate": 1.5,
                                                                                    "cost_text": "около $1.50"})
    monkeypatch.setattr(plan, "member_view", lambda: False)
    assert "1.5" in _sse(event)["data"]
    monkeypatch.setattr(plan, "member_view", lambda: True)
    data = _sse(event)["data"]
    assert "1.5" not in data and "$" not in data


@pytest_asyncio.fixture
async def env(tmp_path, monkeypatch):
    from app.orchestrator.agents.runtime import build_runtime, set_runtime
    from app.web.api import create_app
    from app.web.deps import get_session
    from tests import accounts_harness as ah

    ah.configure(monkeypatch)
    engine, factory = await ah.make_engine(tmp_path / "orch-reject-kill.db")
    ah.bind_identity_session(monkeypatch, factory)

    async def _gen():
        async with factory() as s:
            yield s

    runtime = build_runtime()
    set_runtime(runtime)
    app = create_app()
    app.dependency_overrides[get_session] = _gen
    a = await ah.make_account(factory, email="rk-a@studio.local")
    b = await ah.make_account(factory, email="rk-b@studio.local")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield {"client": client, "runtime": runtime, "a": a, "b": b, "factory": factory}
    set_identity(None)
    await runtime.queue.drain(timeout=5.0)
    set_runtime(None)
    await engine.dispose()


async def test_foreign_member_cannot_reject_a_card(env) -> None:
    runtime, client = env["runtime"], env["client"]
    internal_a = scoped_session("card", env["a"].user_id)
    task_id = runtime.tasks.insert(internal_a, "цель A", TaskStatus.waiting_approval.value)
    runtime.supervisor._session_task[internal_a] = task_id
    res = await client.post("/api/orchestrator/chat/card/reject", json={"change_id": f"orch-{task_id}-1"},
                            headers=env["b"].auth)
    assert res.status_code in (404, 409), res.text
    assert runtime.tasks.get(task_id)["status"] == TaskStatus.waiting_approval.value


async def test_kill_of_an_idle_chat_answers_and_touches_no_foreign_project(env) -> None:
    client = env["client"]
    res = await client.post("/api/orchestrator/chat/main/kill", json={}, headers=env["a"].auth)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["ok"] is True and body["session_id"] == "main" and body["task_id"] is None
    # Проект, которого арендатор не видит, стоп не трогает: 404 до любого действия.
    foreign = await client.post("/api/orchestrator/chat/main/kill", json={"project_id": 999999},
                                headers=env["a"].auth)
    assert foreign.status_code == 404, foreign.text


async def test_member_reaches_the_chat_notice_feed(env) -> None:
    """Лента уведомлений под чатом открыта участнику (строки — его, RLS на Postgres)."""
    res = await env["client"].get("/api/chat-notices", headers=env["a"].auth)
    assert res.status_code == 200, res.text
    assert res.json()["notices"] == []
    missing = await env["client"].post("/api/chat-notices/999999/read", headers=env["a"].auth)
    assert missing.status_code == 404


async def test_chat_notice_feed_hides_money_from_member_only(env, monkeypatch, tmp_path) -> None:
    """Список и «прочитано» ленты уведомлений: участнику — без сумм, админу — как есть."""
    from app.services import chat_notice
    from app.settings import settings

    monkeypatch.setattr(settings, "sqlite_path", tmp_path / "notices-state.db")
    chat_notice.reset()
    _status, made = await chat_notice.send(None, "warn", "Потолок", "Потрачено 48.20 USD из $50", kind="spend_cap")
    assert made is not None
    client = env["client"]
    member = await client.get("/api/chat-notices", headers=env["a"].auth)
    assert member.status_code == 200, member.text
    assert "48.20" not in member.text and "$50" not in member.text
    read = await client.post(f"/api/chat-notices/{made['id']}/read", headers=env["a"].auth)
    assert read.status_code == 200 and "48.20" not in read.text
    admin = await client.get("/api/chat-notices", headers=await _admin_headers(env))
    assert "48.20 USD" in admin.text


async def _admin_headers(env) -> dict:
    from tests import accounts_harness as ah

    if "admin" not in env:
        env["admin"] = await ah.make_admin(env["factory"], email="rk-admin@studio.local")
    return env["admin"].auth


def test_card_binding_is_per_tenant() -> None:
    """Привязка карточка → план в памяти процесса: id карточки другого клиента её не находит."""
    from app.services import orch_bridge
    from app.services.tenant import tenant_scope

    a, b = "11111111-1111-1111-1111-111111111111", "22222222-2222-2222-2222-222222222222"
    with tenant_scope(a):
        orch_bridge._BOUND[orch_bridge._bound_key("orch-501-1")] = "plan-a"
    try:
        with tenant_scope(b):
            assert orch_bridge._bound_key("orch-501-1") not in orch_bridge._BOUND
        with tenant_scope(a):
            assert orch_bridge._BOUND[orch_bridge._bound_key("orch-501-1")] == "plan-a"
    finally:
        orch_bridge._BOUND.clear()


async def test_stale_reject_revokes_the_server_card_not_the_client_id(env, monkeypatch) -> None:
    """Протухшая карточка B: reject закрывает карточку B (её id решает сервер), а не id из тела.

    Раньше revoke брал change_id клиента: B, прислав id карточки A, снимал привязку A и
    писал отзыв с планом A.
    """
    from app.orchestrator.agents.supervisor import _appr_key
    from app.services import orch_bridge
    from app.services.tenant import tenant_scope

    runtime, client = env["runtime"], env["client"]
    a_id, b_id = env["a"].user_id, env["b"].user_id
    with tenant_scope(a_id):
        orch_bridge._BOUND[orch_bridge._bound_key("orch-777-1")] = "plan-of-a"
    internal_b = scoped_session("stale", b_id)
    task_b = runtime.tasks.insert(internal_b, "цель B", TaskStatus.waiting_approval.value)
    runtime.supervisor._session_task[internal_b] = task_b
    runtime.supervisor._stale_cards.add(task_b)
    runtime.supervisor._appr_open[_appr_key(task_b)] = f"orch-{task_b}-1"
    revoked: list[tuple[str, str | None]] = []

    async def _revoke(plan_id, change_id, actor):
        revoked.append((plan_id, change_id))

    monkeypatch.setattr(orch_bridge, "revoke", _revoke)
    try:
        res = await client.post("/api/orchestrator/chat/stale/reject", json={"change_id": "orch-777-1"},
                                headers=env["b"].auth)
        assert res.status_code == 200, res.text
        assert res.json()["approval_id"] == f"orch-{task_b}-1"
        assert revoked == []  # у карточки B плана нет; план A не тронут
        with tenant_scope(a_id):
            assert orch_bridge._BOUND[orch_bridge._bound_key("orch-777-1")] == "plan-of-a"
    finally:
        orch_bridge._BOUND.clear()
