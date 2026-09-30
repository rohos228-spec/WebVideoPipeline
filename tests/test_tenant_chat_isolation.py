from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.settings import settings
from app.web.api import create_app
from app.web.deps import get_session
from tests import accounts_harness as ah


@pytest_asyncio.fixture
async def env(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    ah.configure(monkeypatch)
    engine, factory = await ah.make_engine(tmp_path / "identity.db")
    ah.bind_identity_session(monkeypatch, factory)

    async def _gen():
        async with factory() as s:
            yield s

    app = create_app()
    app.dependency_overrides[get_session] = _gen

    member_a = await ah.make_account(factory, email="user_a@studio.local")
    member_b = await ah.make_account(factory, email="user_b@studio.local")
    admin = await ah.make_admin(factory)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield {
            "client": c,
            "member_a": member_a,
            "member_b": member_b,
            "admin": admin,
        }
    await engine.dispose()


@pytest.mark.asyncio
async def test_tenant_chat_session_isolation(env) -> None:
    client = env["client"]
    auth_a = env["member_a"].auth
    auth_b = env["member_b"].auth

    # 1. Member A creates a session in Chat
    create_res = await client.post(
        "/api/gpt-workspace/sessions",
        json={"title": "Secret Project of User A"},
        headers=auth_a,
    )
    assert create_res.status_code == 200, create_res.text
    session_a = create_res.json()
    sid_a = session_a["id"]

    # 2. Member A lists sessions -> sees their session
    list_a = await client.get("/api/gpt-workspace/sessions", headers=auth_a)
    assert list_a.status_code == 200
    sids_a = [s["id"] for s in list_a.json()["sessions"]]
    assert sid_a in sids_a

    # 3. Member B lists sessions -> does NOT see Member A's session
    list_b = await client.get("/api/gpt-workspace/sessions", headers=auth_b)
    assert list_b.status_code == 200
    sids_b = [s["id"] for s in list_b.json()["sessions"]]
    assert sid_a not in sids_b

    # 4. Member B cannot access Member A's session directly
    get_b = await client.get(f"/api/gpt-workspace/sessions/{sid_a}", headers=auth_b)
    assert get_b.status_code == 404

    # 5. Member B cannot delete Member A's session
    del_b = await client.delete(f"/api/gpt-workspace/sessions/{sid_a}", headers=auth_b)
    assert del_b.status_code == 404


@pytest.mark.asyncio
async def test_tenant_text_llm_choice_isolation(env) -> None:
    client = env["client"]
    auth_a = env["member_a"].auth
    auth_b = env["member_b"].auth
    auth_admin = env["admin"].auth

    # 1. Check default text-llm status for members and admin
    status_admin = await client.get("/api/text-llm", headers=auth_admin)
    assert status_admin.status_code == 200
    status_a = await client.get("/api/text-llm", headers=auth_a)
    assert status_a.status_code == 200
    assert "models" in status_a.json()

    status_b = await client.get("/api/text-llm", headers=auth_b)
    assert status_b.status_code == 200

    # 2. Member A selects claude-sonnet-5-vibecode
    set_a = await client.put(
        "/api/text-llm",
        json={"provider": "vibecode", "model_id": "claude-sonnet-5-vibecode"},
        headers=auth_a,
    )
    assert set_a.status_code == 200

    # Member A now has active model claude-sonnet-5
    status_a2 = await client.get("/api/text-llm", headers=auth_a)
    assert status_a2.json()["active_model"] == "claude-sonnet-5"

    # 3. Member B selects a different model: gpt-5.6-sol-vibecode
    set_b = await client.put(
        "/api/text-llm",
        json={"provider": "vibecode", "model_id": "gpt-5.6-sol-vibecode"},
        headers=auth_b,
    )
    assert set_b.status_code == 200

    # Member B has gpt-5.6-sol active
    status_b2 = await client.get("/api/text-llm", headers=auth_b)
    assert status_b2.json()["active_model"] == "gpt-5.6-sol"

    # Member A STILL has claude-sonnet-5 active (not overwritten by B!)
    status_a3 = await client.get("/api/text-llm", headers=auth_a)
    assert status_a3.json()["active_model"] == "claude-sonnet-5"
