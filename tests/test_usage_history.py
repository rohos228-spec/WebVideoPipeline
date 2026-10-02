"""Тесты ручки истории расходов и использования моделей (GET /api/billing/usage-history)."""

from __future__ import annotations

import uuid

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.models import CreditEntry, LlmCall, MediaCall
from app.web.api import create_app
from app.web.deps import get_session
from tests import accounts_harness as ah


@pytest_asyncio.fixture
async def env(tmp_path, monkeypatch):
    ah.configure(monkeypatch)
    engine, factory = await ah.make_engine(tmp_path / "usage_test.db")
    ah.bind_identity_session(monkeypatch, factory)

    async def _gen():
        async with factory() as s:
            yield s

    app = create_app()
    app.dependency_overrides[get_session] = _gen

    member = await ah.make_account(factory, email="user@studio.local")
    admin = await ah.make_admin(factory)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield {"client": c, "member": member, "admin": admin, "factory": factory}
    await engine.dispose()


async def test_usage_history_accessible_to_member(env):
    """Участник (member) имеет доступ к истории расходов (не 404), себестоимость скрыта."""
    client: AsyncClient = env["client"]
    member = env["member"]

    res = await client.get("/api/billing/usage-history", headers=member.auth)
    assert res.status_code == 200, res.text
    data = res.json()

    assert data["is_admin"] is False
    assert "summary" in data
    assert "models" in data
    assert "recent" in data
    # Себестоимость для участника скрыта
    assert data["summary"]["total_cost_usd"] is None
    for m in data["models"]:
        assert m["cost_usd"] is None


async def test_usage_history_all_tenants_forbidden_for_member(env):
    """Участник не может запрашивать данные всей студии (?all_tenants=true) -> 403."""
    client: AsyncClient = env["client"]
    member = env["member"]

    res = await client.get("/api/billing/usage-history?all_tenants=true", headers=member.auth)
    assert res.status_code == 403


async def test_usage_history_accessible_to_admin(env):
    """Администратор имеет доступ, видит cost_usd и может запрашивать ?all_tenants=true."""
    client: AsyncClient = env["client"]
    admin = env["admin"]

    res = await client.get("/api/billing/usage-history", headers=admin.auth)
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["is_admin"] is True

    # Запрос всей студии
    res_all = await client.get("/api/billing/usage-history?all_tenants=true", headers=admin.auth)
    assert res_all.status_code == 200, res_all.text
    data_all = res_all.json()
    assert data_all["is_admin"] is True


async def test_usage_history_aggregates_models_and_credits(env):
    """Проверяем сбор моделей по типам: изображения, LLM, проводки."""
    client: AsyncClient = env["client"]
    member = env["member"]
    factory = env["factory"]
    tenant_id = member.user_id

    async with factory() as s:
        # Добавляем MediaCall для фото (Flux 2 Pro)
        s.add(
            MediaCall(
                tenant_id=tenant_id,
                project_id=None,
                provider="kie",
                kind="image",
                model="Flux 2 Pro",
                units=1.0,
                unit="item",
                cost_usd=0.005,
                result="ok",
            )
        )
        # Добавляем MediaCall для видео (Kling 3.0 Pro)
        s.add(
            MediaCall(
                tenant_id=tenant_id,
                project_id=None,
                provider="kie",
                kind="video",
                model="Kling 3.0 Pro",
                units=5.0,
                unit="second",
                cost_usd=0.15,
                result="ok",
            )
        )
        # Добавляем LlmCall (Claude 3.5 Sonnet)
        s.add(
            LlmCall(
                tenant_id=tenant_id,
                project_id=None,
                logical_call_id=str(uuid.uuid4()),
                model="claude-sonnet-5",
                prompt_tokens=500,
                completion_tokens=250,
                total_tokens=750,
                cost_usd=0.005,
                result="ok",
            )
        )
        # Добавляем пополнение баланса купоном
        s.add(
            CreditEntry(
                id=str(uuid.uuid4()),
                tenant_id=tenant_id,
                delta_micro=500_000_000,
                kind="topup",
                memo="Купон TEST1",
            )
        )
        await s.commit()

    res = await client.get("/api/billing/usage-history", headers=member.auth)
    assert res.status_code == 200
    data = res.json()

    models = {m["display_name"]: m for m in data["models"]}
    assert "Flux 2 Pro" in models
    assert models["Flux 2 Pro"]["kind"] == "image"
    assert models["Flux 2 Pro"]["calls"] == 1

    assert "Kling 3.0 Pro" in models
    assert models["Kling 3.0 Pro"]["kind"] == "video"
    assert models["Kling 3.0 Pro"]["calls"] == 1

    assert "Claude Sonnet 5" in models
    assert models["Claude Sonnet 5"]["kind"] == "llm"
    assert models["Claude Sonnet 5"]["calls"] == 1

    # Проверяем непустые категории в сводке
    by_kind = data["summary"]["by_kind"]
    assert by_kind["image"]["calls"] >= 1
    assert by_kind["video"]["calls"] >= 1
    assert by_kind["llm"]["calls"] >= 1

    # Проверяем журнал операций
    recent_kinds = [r["kind"] for r in data["recent"]]
    assert "image" in recent_kinds
    assert "video" in recent_kinds
    assert "llm" in recent_kinds
    assert "topup" in recent_kinds
