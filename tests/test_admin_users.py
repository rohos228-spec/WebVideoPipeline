"""Тесты ручек статистики и управления пользователями: `/api/admin/users`, `/api/admin/overview`."""

from __future__ import annotations

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.models import Artifact, ArtifactKind, MediaCall, Project, StudioUser
from app.web.api import create_app
from app.web.deps import get_session
from tests import accounts_harness as ah


@pytest_asyncio.fixture
async def env(tmp_path, monkeypatch):
    ah.configure(monkeypatch)
    _engine, factory = await ah.make_engine(tmp_path / "admin-users-test.db")
    ah.bind_identity_session(monkeypatch, factory)

    async def _gen():
        async with factory() as s:
            yield s

    app = create_app()
    app.dependency_overrides[get_session] = _gen

    admin = await ah.make_admin(factory)
    member = await ah.make_account(factory, email="alice@studio.local")
    member2 = await ah.make_account(factory, email="bob@studio.local")

    # Создадим проекты и артефакты для member, чтобы проверить подсчет статистики
    async with factory() as session:
        u_alice = await session.get(StudioUser, member.user_id)
        if u_alice:
            u_alice.display_name = "Alice Creator"
        u_bob = await session.get(StudioUser, member2.user_id)
        if u_bob:
            u_bob.display_name = "Bob Worker"

        p1 = Project(tenant_id=member.user_id, slug="alice-proj", title="Alice First Video", topic="Alice Topic 1")
        p2 = Project(tenant_id=member.user_id, slug="alice-proj-2", title="Alice Second Video", topic="Alice Topic 2")
        session.add_all([p1, p2])
        await session.flush()

        # Добавим артефакты (картинки)
        a1 = Artifact(
            project_id=p1.id,
            tenant_id=member.user_id,
            kind=ArtifactKind.scene_image,
            uuid="art-1",
            path="/tmp/f1.png",
        )
        a2 = Artifact(
            project_id=p1.id,
            tenant_id=member.user_id,
            kind=ArtifactKind.scene_video,
            uuid="art-2",
            path="/tmp/c1.mp4",
        )
        session.add_all([a1, a2])

        # Добавим вызовы MediaCall
        m1 = MediaCall(
            tenant_id=member.user_id,
            project_id=p1.id,
            provider="kie",
            kind="image",
            model="kling-image",
            cost_usd=0.05,
            result="ok",
        )
        m2 = MediaCall(
            tenant_id=member.user_id,
            project_id=p1.id,
            provider="kie",
            kind="video",
            model="kling-video",
            cost_usd=0.40,
            result="ok",
        )
        session.add_all([m1, m2])
        await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield {"client": client, "admin": admin, "member": member, "member2": member2}


async def test_admin_lists_users_with_aggregated_stats(env) -> None:
    r = await env["client"].get("/api/admin/users", headers=env["admin"].auth)
    assert r.status_code == 200
    data = r.json()
    assert "items" in data
    assert data["total"] >= 3  # admin + alice + bob

    # Находим Алису
    alice_row = next((u for u in data["items"] if u["email"] == "alice@studio.local"), None)
    assert alice_row is not None
    assert alice_row["name"] == "Alice Creator"
    assert alice_row["projects"] == 2
    assert alice_row["generations"] == 2
    assert alice_row["requests"] == 2
    assert alice_row["cost_usd"] == 0.45


async def test_member_cannot_access_admin_users(env) -> None:
    r = await env["client"].get("/api/admin/users", headers=env["member"].auth)
    assert r.status_code == 404, "Обычный участник не должен иметь доступа к ручке /admin/users"


async def test_admin_user_detail_and_update(env) -> None:
    alice_id = env["member"].user_id
    r = await env["client"].get(f"/api/admin/users/{alice_id}", headers=env["admin"].auth)
    assert r.status_code == 200
    detail = r.json()
    assert detail["email"] == "alice@studio.local"
    assert len(detail["projects_list"]) == 2

    # Обновим роль Алисы до admin
    put_r = await env["client"].put(
        f"/api/admin/users/{alice_id}",
        json={"role": "admin"},
        headers=env["admin"].auth,
    )
    assert put_r.status_code == 200
    assert put_r.json()["role"] == "admin"


async def test_admin_overview_metrics(env) -> None:
    r = await env["client"].get("/api/admin/overview?days=30", headers=env["admin"].auth)
    assert r.status_code == 200
    overview = r.json()
    assert "users" in overview
    assert overview["users"]["total"] >= 3
    assert "projects" in overview
    assert overview["projects"]["total"] >= 2
    assert "generations" in overview
    assert overview["generations"]["images"] >= 1
    assert "top_users" in overview


async def test_admin_plans(env) -> None:
    r = await env["client"].get("/api/admin/plans", headers=env["admin"].auth)
    assert r.status_code == 200
    plans = r.json()
    assert len(plans) >= 2
    assert any(p["id"] == "pro" for p in plans)
