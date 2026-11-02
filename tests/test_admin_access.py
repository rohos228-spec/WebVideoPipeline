"""Доступ к админ-панели: `GET /api/admin/access`.

Ручка — гейт фронта: без неё `/admin` рисует «Раздел не найден» (404), а не
объяснение. Поэтому проверяем два ответа, которые видит человек: админ получает
`is_admin=true` и свой портрет, участник — 200 с `is_admin=false` и причиной
(401/404 заставили бы страницу показать ошибку вместо «нужен вход админа»),
а сам расходный раздел участнику по-прежнему закрыт 404.
"""

from __future__ import annotations

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.web.api import create_app
from app.web.deps import get_session
from tests import accounts_harness as ah


@pytest_asyncio.fixture
async def env(tmp_path, monkeypatch):
    ah.configure(monkeypatch)
    _engine, factory = await ah.make_engine(tmp_path / "admin-access.db")
    ah.bind_identity_session(monkeypatch, factory)

    async def _gen():
        async with factory() as s:
            yield s

    app = create_app()
    app.dependency_overrides[get_session] = _gen
    member = await ah.make_account(factory, email="member@studio.local")
    admin = await ah.make_admin(factory)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield {"client": client, "admin": admin, "member": member}


async def test_admin_gets_own_rights(env) -> None:
    r = await env["client"].get("/api/admin/access", headers=env["admin"].auth)
    assert r.status_code == 200
    body = r.json()
    assert body["is_admin"] is True
    assert body["auth_required"] is True
    assert body["bootstrap"] is False
    assert body["user"]["email"]
    assert body["user"]["role"] == "admin"


async def test_member_gets_explanation_and_costs_stay_closed(env) -> None:
    r = await env["client"].get("/api/admin/access", headers=env["member"].auth)
    assert r.status_code == 200, "гейт панели должен объяснять, а не падать"
    body = r.json()
    assert body["is_admin"] is False
    assert body["reason"]

    costs = await env["client"].get("/api/admin/costs/overview", headers=env["member"].auth)
    assert costs.status_code == 404
