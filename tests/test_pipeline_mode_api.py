"""Изоляция механик v1/v2 на уровне API.

Контракт (docs/UNIFIED-MECHANICS-PLAN.md, R1): режим назначается при
создании и immutable; списки режутся запросом (WHERE), а не прячутся
фронтом; дети наследуют режим родителя.
"""

from __future__ import annotations

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base, Project, ProjectStatus
from app.services.mass_factory import COPY_PROJECT_FIELDS
from app.settings import settings
from app.web.api import create_app
from app.web.deps import get_session


@pytest_asyncio.fixture
async def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "allow_unisolated_tenants", True)
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'modes.db'}", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as s:
        s.add(Project(slug="classic", topic="t", status=ProjectStatus.new, pipeline_mode="v1"))
        s.add(Project(slug="director", topic="t", status=ProjectStatus.new, pipeline_mode="v2"))
        await s.commit()

    async def _gen():
        async with factory() as s:
            yield s

    app = create_app()
    app.dependency_overrides[get_session] = _gen
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    await engine.dispose()


async def test_list_returns_modes(client) -> None:
    res = await client.get("/api/projects")
    assert res.status_code == 200
    by_slug = {p["slug"]: p for p in res.json()}
    assert by_slug["classic"]["pipeline_mode"] == "v1"
    assert by_slug["director"]["pipeline_mode"] == "v2"


async def test_list_filters_by_mode(client) -> None:
    v1 = (await client.get("/api/projects", params={"pipeline_mode": "v1"})).json()
    assert {p["slug"] for p in v1} == {"classic"}
    v2 = (await client.get("/api/projects", params={"pipeline_mode": "v2"})).json()
    assert {p["slug"] for p in v2} == {"director"}


async def test_list_rejects_unknown_mode(client) -> None:
    res = await client.get("/api/projects", params={"pipeline_mode": "v3"})
    assert res.status_code == 400


async def test_create_defaults_to_v1(client) -> None:
    res = await client.post("/api/projects", json={"title": "Новый"})
    assert res.status_code == 201, res.text
    assert res.json()["pipeline_mode"] == "v1"


async def test_create_v2_and_reject_garbage(client) -> None:
    res = await client.post("/api/projects", json={"title": "Режиссёрский", "pipeline_mode": "v2"})
    assert res.status_code == 201, res.text
    assert res.json()["pipeline_mode"] == "v2"
    bad = await client.post("/api/projects", json={"title": "Мусор", "pipeline_mode": "v3"})
    assert bad.status_code == 422


async def test_patch_mode_is_rejected_and_kept(client) -> None:
    projects = {p["slug"]: p for p in (await client.get("/api/projects")).json()}
    pid = projects["classic"]["id"]
    res = await client.patch(f"/api/projects/{pid}", json={"pipeline_mode": "v2"})
    assert res.status_code == 400
    again = await client.get(f"/api/projects/{pid}")
    assert again.json()["pipeline_mode"] == "v1"


def test_children_inherit_mode_contract() -> None:
    """Дочерние и batch-проекты копируют режим (единый список полей)."""
    assert "pipeline_mode" in COPY_PROJECT_FIELDS
