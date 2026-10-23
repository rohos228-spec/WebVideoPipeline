"""Тесты для роутера профиля `/api/profile/*`."""

from __future__ import annotations

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.models import Project, ProjectStatus
from app.settings import settings
from app.web.api import create_app
from app.web.deps import get_session
from tests import accounts_harness as ah


@pytest_asyncio.fixture
async def env(tmp_path, monkeypatch):
    ah.configure(monkeypatch)
    _engine, factory = await ah.make_engine(tmp_path / "profile-test.db")
    ah.bind_identity_session(monkeypatch, factory)

    async def _gen():
        async with factory() as s:
            yield s

    app = create_app()
    app.dependency_overrides[get_session] = _gen

    member = await ah.make_account(factory, email="user@studio.local")
    admin = await ah.make_admin(factory, email="admin@studio.local")

    monkeypatch.setattr(settings, "kie_api_key", "mock-kie-key-test-1234")  # gitleaks:allow
    monkeypatch.setattr(settings, "elevenlabs_api_key", "mock-eleven-key-test-5678")  # gitleaks:allow

    # Создаём проект участника
    async with factory() as session:
        proj = Project(
            slug="member-project",
            topic="Member Test",
            tenant_id=member.user_id,
            status=ProjectStatus.assembled,
        )
        session.add(proj)
        await session.commit()
        await session.refresh(proj)
        proj_id = proj.id

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield {
            "client": client,
            "member": member,
            "admin": admin,
            "project_id": proj_id,
        }


async def test_profile_get_member(env) -> None:
    client = env["client"]
    member = env["member"]

    r = await client.get("/api/profile", headers=member.auth)
    assert r.status_code == 200
    data = r.json()
    assert data["handle"] == "user"
    assert data["plan"] == "Free"


async def test_profile_keys_member(env) -> None:
    client = env["client"]
    member = env["member"]

    r = await client.get("/api/profile/keys", headers=member.auth)
    assert r.status_code == 200
    keys = r.json()
    by_id = {k["id"]: k for k in keys}

    assert "kie" in by_id
    assert by_id["kie"]["configured"] is True
    assert by_id["kie"]["masked"] == "moc...1234"

    assert "elevenlabs" in by_id
    assert by_id["elevenlabs"]["configured"] is True
    assert by_id["elevenlabs"]["masked"] == "moc...5678"


async def test_profile_stats_member(env) -> None:
    client = env["client"]
    member = env["member"]

    r = await client.get("/api/profile/stats", headers=member.auth)
    assert r.status_code == 200
    data = r.json()
    assert data["projects"] == 1
    assert data["completed_videos"] == 1
    assert len(data["daily"]) == 30


async def test_profile_update_display_name(env) -> None:
    client = env["client"]
    member = env["member"]

    r = await client.put("/api/profile", json={"name": "Alex Director"}, headers=member.auth)
    assert r.status_code == 200
    data = r.json()
    assert data["name"] == "Alex Director"


async def test_profile_keys_update_forbidden_for_member(env) -> None:
    client = env["client"]
    member = env["member"]

    r = await client.put("/api/profile/keys/kie", json={"value": "new-kie-key"}, headers=member.auth)
    assert r.status_code == 403


async def test_profile_keys_update_allowed_for_admin(env) -> None:
    client = env["client"]
    admin = env["admin"]

    r = await client.put("/api/profile/keys/kie", json={"value": "admin-mock-key-12345"}, headers=admin.auth)  # gitleaks:allow
    assert r.status_code == 200
    data = r.json()
    assert data["configured"] is True
    assert data["masked"] == "adm...2345"
