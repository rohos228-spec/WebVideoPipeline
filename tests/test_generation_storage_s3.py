from __future__ import annotations

import json
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base
from app.services.generation_storage import (
    build_generation_path,
    delete_generation_item,
    list_generation_files,
    publish_generation_file,
    sync_pending_generations_to_s3,
    write_sidecar,
)
from app.settings import settings


class _MockStorage:
    enabled = True

    def __init__(self) -> None:
        self.uploaded: list[tuple[str, Path]] = []
        self.deleted: list[str] = []

    async def put_file(self, key: str, source: Path, *, content_type: str = "") -> str:
        self.uploaded.append((key, source))
        return key

    async def delete(self, key: str) -> None:
        self.deleted.append(key)

    def url(self, key: str, *, ttl_sec: int = 900, download_name: str = "") -> str:
        return f"https://storage.yandexcloud.net/test2videogen/{key}?signed=1"


@pytest.fixture
def mock_storage(monkeypatch):
    ms = _MockStorage()
    monkeypatch.setattr("app.services.storage.get_storage", lambda: ms)
    monkeypatch.setattr("app.services.generation_storage.get_storage", lambda: ms)
    monkeypatch.setattr("app.services.artifact_storage.get_storage", lambda: ms)

    return ms


@pytest.mark.asyncio
async def test_publish_generation_file_flow(tmp_path: Path, monkeypatch, mock_storage) -> None:
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "yandex_storage_endpoint", "https://storage.yandexcloud.net")
    monkeypatch.setattr(settings, "yandex_storage_access_key", "test-key")
    monkeypatch.setattr(settings, "yandex_storage_secret_key", "test-secret")
    monkeypatch.setattr(settings, "yandex_storage_bucket", "test-bucket")

    # Создаём файл генерации
    img_path = build_generation_path(media="image", model="flux-pro", ext=".png")
    img_path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
    write_sidecar(
        img_path,
        media="image",
        model="flux-pro",
        prompt="beautiful sunset",
        provider="outsee",
        status="done",
    )

    # 1. Выгрузка в S3
    key = await publish_generation_file(img_path, media="image", model="flux-pro", tenant_id="alice")
    assert key.startswith("studio/alice/generations/image/flux-pro/")
    assert len(mock_storage.uploaded) >= 1

    # Проверяем, что в sidecar прописался storage_key
    side = img_path.with_suffix(".json")
    meta = json.loads(side.read_text(encoding="utf-8"))
    assert meta.get("storage_key") == key

    # 2. Идемпотентность: повторная выгрузка ничего не делает
    calls_before = len(mock_storage.uploaded)
    key2 = await publish_generation_file(img_path, media="image", model="flux-pro", tenant_id="alice")
    assert key2 == key
    assert len(mock_storage.uploaded) == calls_before

    # 3. Список генераций содержит storage_key
    items = list_generation_files(kind="image")
    match = next((i for i in items if i["id"] == f"gen-{img_path.name}"), None)
    assert match is not None
    assert match.get("storage_key") == key

    # 4. Удаление удаляет и из S3
    ok = await delete_generation_item(path=str(img_path))
    assert ok is True
    assert key in mock_storage.deleted
    assert not img_path.exists()
    assert not side.exists()


@pytest.mark.asyncio
async def test_serve_data_file_redirects_to_s3(tmp_path: Path, monkeypatch, mock_storage) -> None:
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "yandex_storage_endpoint", "https://storage.yandexcloud.net")
    monkeypatch.setattr(settings, "yandex_storage_access_key", "test-key")
    monkeypatch.setattr(settings, "yandex_storage_secret_key", "test-secret")
    monkeypatch.setattr(settings, "yandex_storage_bucket", "test-bucket")

    # Создаём файл генерации и sidecar с storage_key
    img_path = build_generation_path(media="image", model="kling", ext=".png")
    img_path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
    write_sidecar(
        img_path,
        media="image",
        model="kling",
        prompt="cool prompt",
        provider="kie",
        status="done",
        storage_key="studio/owner/generations/image/kling/sample.png",
    )

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    from app.web.api import create_app
    from app.web.deps import get_session

    async def _gen():
        async with factory() as s:
            yield s

    app = create_app()
    app.dependency_overrides[get_session] = _gen

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Без thumb -> редирект 307 на подписанный URL в S3
        res = await client.get(f"/api/files?path={img_path.resolve()}", follow_redirects=False)
        assert res.status_code == 307
        assert res.headers["location"].startswith("https://storage.yandexcloud.net/test2videogen/")

    await engine.dispose()


@pytest.mark.asyncio
async def test_sync_pending_generations(tmp_path: Path, monkeypatch, mock_storage) -> None:
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "yandex_storage_endpoint", "https://storage.yandexcloud.net")
    monkeypatch.setattr(settings, "yandex_storage_access_key", "test-key")
    monkeypatch.setattr(settings, "yandex_storage_secret_key", "test-secret")
    monkeypatch.setattr(settings, "yandex_storage_bucket", "test-bucket")

    # Создаём 2 файла без storage_key
    p1 = build_generation_path(media="image", model="midjourney", ext=".png")
    p1.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
    write_sidecar(p1, media="image", model="midjourney", prompt="p1", status="done")

    p2 = build_generation_path(media="image", model="midjourney", ext=".png")
    p2.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
    write_sidecar(p2, media="image", model="midjourney", prompt="p2", status="done")

    count = await sync_pending_generations_to_s3(limit=10)
    assert count == 2

    # Повторный вызов видит, что все уже выгружены
    count2 = await sync_pending_generations_to_s3(limit=10)
    assert count2 == 0
