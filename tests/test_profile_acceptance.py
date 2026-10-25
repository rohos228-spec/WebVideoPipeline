"""Приём ответа и учёт: живой ASGI-прогон профиля / ratings / stats."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.models import MediaCall, Project, ProjectStatus
from app.settings import settings
from app.web.api import create_app
from app.web.deps import get_session
from tests import accounts_harness as ah


@pytest_asyncio.fixture
async def env(tmp_path, monkeypatch):
    ah.configure(monkeypatch)
    _engine, factory = await ah.make_engine(tmp_path / "profile-accept.db")
    ah.bind_identity_session(monkeypatch, factory)
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)

    async def _gen():
        async with factory() as s:
            yield s

    app = create_app()
    app.dependency_overrides[get_session] = _gen

    member = await ah.make_account(factory, email="member@studio.local")
    admin = await ah.make_admin(factory, email="admin@studio.local")

    async with factory() as session:
        proj = Project(
            slug="acc-project",
            topic="Accounting",
            tenant_id=member.user_id,
            status=ProjectStatus.assembled,
        )
        session.add(proj)
        await session.flush()
        now = datetime.now(UTC)
        session.add_all(
            [
                MediaCall(
                    tenant_id=member.user_id,
                    project_id=proj.id,
                    provider="kie",
                    kind="image",
                    model="test",
                    units=1,
                    result="ok",
                    created_at=now,
                ),
                MediaCall(
                    tenant_id=member.user_id,
                    project_id=proj.id,
                    provider="kie",
                    kind="video",
                    model="test",
                    units=1,
                    result="ok",
                    created_at=now,
                ),
                MediaCall(
                    tenant_id=member.user_id,
                    project_id=proj.id,
                    provider="elevenlabs",
                    kind="tts",
                    model="test",
                    units=10,
                    result="ok",
                    created_at=now,
                ),
                MediaCall(
                    tenant_id=member.user_id,
                    project_id=proj.id,
                    provider="kie",
                    kind="image",
                    model="test",
                    units=1,
                    result="error",
                    created_at=now,
                ),
            ]
        )
        await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield {"client": client, "member": member, "admin": admin, "tmp_path": tmp_path}


async def test_accept_profile_roundtrip(env) -> None:
    c, m = env["client"], env["member"]
    r = await c.put(
        "/api/profile",
        headers=m.auth,
        json={
            "name": "Учётчик",
            "handle": "accountant",
            "bio": "Проверка приёма",
            "notifications": {"email": True, "browser": False},
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["name"] == "Учётчик"
    assert body["handle"] == "accountant"
    assert body["bio"] == "Проверка приёма"
    assert body["notifications"] == {"email": True, "browser": False}
    assert body["avatar_url"] is None
    assert "plan" in body and "created_at" in body

    r2 = await c.get("/api/profile", headers=m.auth)
    assert r2.status_code == 200
    assert r2.json()["handle"] == "accountant"


async def test_accept_avatar_bytes_roundtrip(env) -> None:
    c, m = env["client"], env["member"]
    png = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00"
        b"\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    up = await c.post(
        "/api/profile/avatar",
        headers=m.auth,
        files={"file": ("a.png", png, "image/png")},
    )
    assert up.status_code == 200, up.text
    assert up.json()["avatar_url"] == "/api/profile/avatar-file"
    raw = await c.get("/api/profile/avatar-file", headers=m.auth)
    assert raw.status_code == 200
    assert raw.content[:8] == b"\x89PNG\r\n\x1a\n"


async def test_accept_stats_accounting_from_media_calls(env) -> None:
    c, m = env["client"], env["member"]
    r = await c.get("/api/profile/stats", headers=m.auth)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["projects"] == 1
    assert data["completed_videos"] == 1
    assert len(data["daily"]) == 30
    today = data["daily"][-1]
    # error media_call must not count; ok image/video/tts must
    assert today["images"] == 1
    assert today["videos"] == 1
    assert today["audio"] == 1
    assert today["finals"] >= 1


async def test_accept_keys_persist_file(env) -> None:
    c, a = env["client"], env["admin"]
    r = await c.put(
        "/api/profile/keys/wavespeed",
        headers=a.auth,
        json={"value": "persist-wavespeed-key-9999"},  # gitleaks:allow
    )
    assert r.status_code == 200, r.text
    assert r.json()["configured"] is True
    path = env["tmp_path"] / "data" / "studio_api_keys.json"
    assert path.is_file()
    assert "persist-wavespeed-key-9999" in path.read_text(encoding="utf-8")


async def test_accept_ratings_and_node_reviews_shape(env) -> None:
    c, m = env["client"], env["member"]
    r = await c.get("/api/ratings?kind=node", headers=m.auth)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["default"] == 5
    assert body["ratings"] == {}

    r2 = await c.get("/api/ratings?kind=prompt", headers=m.auth)
    assert r2.status_code == 200
    assert "ratings" in r2.json()

    r3 = await c.get("/api/node-reviews", headers=m.auth)
    assert r3.status_code == 200, r3.text
    assert r3.json()["items"] == []
