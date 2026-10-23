"""Тесты для роутера хранилища `/api/storage/*`."""

from __future__ import annotations

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.models import Project
from app.settings import settings
from app.web.api import create_app
from app.web.deps import get_session
from tests import accounts_harness as ah


@pytest_asyncio.fixture
async def env(tmp_path, monkeypatch):
    ah.configure(monkeypatch)
    _engine, factory = await ah.make_engine(tmp_path / "storage-test.db")
    ah.bind_identity_session(monkeypatch, factory)

    async def _gen():
        async with factory() as s:
            yield s

    app = create_app()
    app.dependency_overrides[get_session] = _gen
    member = await ah.make_account(factory, email="member@studio.local")
    admin = await ah.make_admin(factory)

    # Создаём тестовый проект
    async with factory() as session:
        proj = Project(slug="test-storage-proj", topic="Storage Test")
        session.add(proj)
        await session.commit()
        await session.refresh(proj)
        proj_id = proj.id

    # Создадим пару файлов в папке проекта
    proj_dir = tmp_path / "videos" / "test-storage-proj"
    proj_dir.mkdir(parents=True, exist_ok=True)
    (proj_dir / "test.mp4").write_bytes(b"dummy video content" * 100)
    (proj_dir / "test.png").write_bytes(b"dummy image content" * 50)
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield {
            "client": client,
            "admin": admin,
            "member": member,
            "project_id": proj_id,
            "tmp_path": tmp_path,
        }


async def test_storage_usage_access_control(env) -> None:
    # 1. Обычный участник не имеет доступа (404 согласно IdentityMiddleware)
    r_member = await env["client"].get("/api/storage/usage", headers=env["member"].auth)
    assert r_member.status_code == 404

    # 2. Администратор получает статистику
    r_admin = await env["client"].get("/api/storage/usage", headers=env["admin"].auth)
    assert r_admin.status_code == 200
    data = r_admin.json()
    assert "total_bytes" in data
    assert "by_type" in data
    assert "video" in data["by_type"]
    assert "image" in data["by_type"]
    assert "projects" in data
    assert "disk" in data
    assert len(data["projects"]) >= 1


async def test_storage_cache_and_clear(env) -> None:
    tmp = env["tmp_path"]
    thumbs = tmp / ".thumbs"
    thumbs.mkdir(parents=True, exist_ok=True)
    (thumbs / "thumb1.webp").write_bytes(b"thumb data" * 10)

    # 1. Проверяем подсчёт кэша
    r = await env["client"].get("/api/storage/cache", headers=env["admin"].auth)
    assert r.status_code == 200
    cache_info = r.json()
    assert cache_info["total_bytes"] > 0
    assert len(cache_info["dirs"]) > 0

    # 2. Очищаем кэш
    r_clear = await env["client"].post("/api/storage/cache/clear", headers=env["admin"].auth)
    assert r_clear.status_code == 200
    clear_info = r_clear.json()
    assert clear_info["freed_bytes"] > 0
    assert clear_info["deleted_files"] >= 1


async def test_storage_export_and_download(env) -> None:
    proj_id = env["project_id"]

    # 1. Запрос на экспорт
    r_export = await env["client"].post(f"/api/storage/export/{proj_id}", headers=env["admin"].auth)
    assert r_export.status_code == 200
    exp = r_export.json()
    assert "path" in exp
    assert "url" in exp
    assert exp["bytes"] > 0

    # 2. Скачивание архива
    r_dl = await env["client"].get(exp["url"], headers=env["admin"].auth)
    assert r_dl.status_code == 200
    assert len(r_dl.content) == exp["bytes"]
