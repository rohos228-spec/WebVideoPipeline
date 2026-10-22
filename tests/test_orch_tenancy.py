"""Арендатор в движке оркестратора без Postgres: имена сессий и фоновые проходы.

Строки движка изолирует RLS (это проверяет `tests/test_orch_tenant_postgres.py`
на живом сервере). Здесь — то, что живёт в памяти процесса и от RLS не
зависит: два клиента, назвавшие чат одинаково, получают разные сессии
движка, ответы ручек показывают клиенту его внешнее имя, а фоновый проход в
режиме владельца остаётся одним проходом без арендатора.
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.orchestrator.contracts import OrchEvent
from app.services.orch_tenancy import (
    for_each_tenant,
    owns_session,
    public_session,
    scoped_session,
    session_tenant,
    tenants_to_serve,
)
from app.services.tenant import current_tenant, tenant_scope


def test_scoped_session_carries_the_tenant_and_round_trips() -> None:
    tenant = str(uuid.uuid4())
    internal = scoped_session("main", tenant)
    assert internal != "main"
    assert public_session(internal) == "main"
    assert session_tenant(internal) == tenant
    with tenant_scope(tenant):
        assert scoped_session("main") == internal
        assert owns_session(internal)
    assert not owns_session(internal), "владелец не владеет сессией арендатора"


def test_owner_mode_keeps_the_session_name() -> None:
    """Без арендатора имя не меняется: поведение студии владельца прежнее."""
    assert current_tenant() is None
    assert scoped_session("main") == "main"
    assert public_session("main") == "main"
    assert session_tenant("main") is None
    assert owns_session("main")


def test_two_tenants_never_share_a_session() -> None:
    a, b = str(uuid.uuid4()), str(uuid.uuid4())
    assert scoped_session("main", a) != scoped_session("main", b)


def test_external_alphabet_cannot_forge_an_internal_name() -> None:
    """Разделитель не входит в алфавит внешнего имени — чужое имя не собрать."""
    from app.services.orch_tenancy import SEPARATOR
    from app.web.routers.orchestrator_chat import _SESSION_RE

    tenant = str(uuid.uuid4())
    assert not _SESSION_RE.fullmatch(scoped_session("main", tenant))
    assert not _SESSION_RE.fullmatch(f"main{SEPARATOR}x")


async def test_for_each_tenant_is_one_owner_pass_on_sqlite() -> None:
    assert await tenants_to_serve() == [None]
    seen: list[str | None] = []

    async def _job() -> list[int]:
        seen.append(current_tenant())
        return [1]

    assert await for_each_tenant(_job, what="test") == [1]
    assert seen == [None]


async def test_for_each_tenant_survives_a_failing_pass() -> None:
    async def _boom() -> list[int]:
        raise RuntimeError("сбой прохода")

    assert await for_each_tenant(_boom, what="test") == []


def test_sse_shows_the_public_session() -> None:
    from app.web.routers.orchestrator_chat import _public, _sse

    internal = scoped_session("main", str(uuid.uuid4()))
    event = OrchEvent(type="message", seq=3, session_id=internal, payload={})
    data = _sse(event)["data"]
    assert '"session_id":"main"' in data.replace(" ", "")
    assert internal not in data
    assert _public({"session": internal, "task_id": 1}) == {"session": "main", "task_id": 1}


# ── ручки чата в режиме учётных записей ─────────────────────────────────────


@pytest_asyncio.fixture
async def env(tmp_path, monkeypatch):
    from app.orchestrator.agents.runtime import build_runtime, set_runtime
    from app.web.api import create_app
    from app.web.deps import get_session
    from tests import accounts_harness as ah

    ah.configure(monkeypatch)
    engine, factory = await ah.make_engine(tmp_path / "orch-tenancy.db")
    ah.bind_identity_session(monkeypatch, factory)

    async def _gen():
        async with factory() as s:
            yield s

    runtime = build_runtime()
    set_runtime(runtime)
    app = create_app()
    app.dependency_overrides[get_session] = _gen
    a = await ah.make_account(factory, email="chat-a@studio.local")
    b = await ah.make_account(factory, email="chat-b@studio.local")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield {"client": client, "runtime": runtime, "a": a, "b": b}
    await runtime.queue.drain(timeout=5.0)
    set_runtime(None)
    await engine.dispose()


async def test_same_chat_name_is_a_different_session_per_client(env) -> None:
    """A и B назвали чат «main»: задача A не видна B ни в статусе, ни в правках."""
    from app.orchestrator.contracts import TaskStatus

    runtime, client = env["runtime"], env["client"]
    a = env["a"].user_id
    task_id = runtime.tasks.insert(scoped_session("main", a), "цель A", TaskStatus.waiting_approval.value)
    runtime.supervisor._session_task[scoped_session("main", a)] = task_id

    status_a = await client.get("/api/orchestrator/chat/main/status", headers=env["a"].auth)
    status_b = await client.get("/api/orchestrator/chat/main/status", headers=env["b"].auth)
    assert status_a.status_code == status_b.status_code == 200
    assert status_a.json()["session"] == "main" and status_a.json()["task_id"] == task_id
    assert status_b.json()["task_id"] is None

    path = f"/api/orchestrator/chat/main/partial-changes?task_id={task_id}"
    assert (await client.get(path, headers=env["b"].auth)).status_code == 404
    assert (await client.get(path, headers=env["a"].auth)).status_code == 200
    resume_b = await client.post("/api/orchestrator/chat/main/resume", headers=env["b"].auth)
    assert resume_b.status_code == 404


async def test_member_message_goes_to_the_tenant_session(env) -> None:
    runtime, client = env["runtime"], env["client"]
    res = await client.post(
        "/api/orchestrator/chat/main/message", json={"text": "привет"}, headers=env["a"].auth
    )
    assert res.status_code == 200, res.text
    assert res.json()["session"] == "main"
    await runtime.queue.drain(timeout=5.0)
    internal_a = scoped_session("main", env["a"].user_id)
    internal_b = scoped_session("main", env["b"].user_id)
    assert await runtime.bus.replay_from(internal_a, 0), "реплика A не попала в её сессию"
    assert await runtime.bus.replay_from(internal_b, 0) == []
    assert await runtime.bus.replay_from("main", 0) == [], "сессия владельца не тронута"


@pytest.mark.parametrize("decision", [None, "deny"])
async def test_member_cannot_decide_on_a_foreign_card(env, decision) -> None:
    """Карточка одобрения A для B не существует: решение B её не трогает."""
    from app.orchestrator.contracts import TaskStatus

    runtime, client = env["runtime"], env["client"]
    a = env["a"].user_id
    internal_a = scoped_session("card", a)
    task_id = runtime.tasks.insert(internal_a, "цель A", TaskStatus.waiting_approval.value)
    body = {"change_id": f"orch-{task_id}-1"}
    if decision is not None:
        body["decision"] = decision
    res = await client.post("/api/orchestrator/chat/card/approve", json=body, headers=env["b"].auth)
    assert res.status_code in (404, 409), res.text
    assert runtime.tasks.get(task_id)["status"] == TaskStatus.waiting_approval.value
