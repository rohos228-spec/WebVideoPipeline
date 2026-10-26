"""Tests for node background upload and project cover endpoint."""

from __future__ import annotations

import io
import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Artifact, ArtifactKind, Base, Project, ProjectStatus
from app.web.api import create_app
from app.web.deps import get_session

app = create_app()


@pytest_asyncio.fixture
async def cover_client(tmp_path):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'covers_test.db'}"
    engine = create_async_engine(db_url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _gen():
        async with factory() as s:
            yield s

    app.dependency_overrides[get_session] = _gen

    async with factory() as session:
        p = Project(
            topic="Cover Project",
            slug=f"test-cover-{uuid.uuid4().hex[:8]}",
            status=ProjectStatus.planning,
        )
        session.add(p)
        await session.flush()
        pid = p.id
        proj_dir = p.data_dir
        proj_dir.mkdir(parents=True, exist_ok=True)
        await session.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c, pid, proj_dir, factory

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_node_bg_upload(cover_client):
    c, pid, proj_dir, _ = cover_client
    img_data = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDRfakeimagecontent"
    files = {"file": ("my_bg.png", io.BytesIO(img_data), "image/png")}

    r = await c.post(f"/api/projects/{pid}/node-bg?node_key=script_1", files=files)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["ok"] is True
    assert "/api/files?path=" in data["preview_url"]

    # Verify file saved on disk
    saved_file = proj_dir / "node_backgrounds" / "script_1.png"
    assert saved_file.is_file()
    assert saved_file.read_bytes() == img_data


@pytest.mark.asyncio
async def test_node_bg_upload_s3(cover_client, monkeypatch):
    c, pid, proj_dir, _ = cover_client
    img_data = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDRfakeimagecontent"
    files = {"file": ("my_bg.png", io.BytesIO(img_data), "image/png")}

    uploaded = {}

    class FakeStorage:
        enabled = True

        async def put_file(self, key, path, content_type=""):
            uploaded[key] = (path, content_type)
            return key

    from app.settings import settings

    monkeypatch.setattr(settings, "s3_access_key", "test_key")
    monkeypatch.setattr(settings, "s3_secret_key", "test_secret")
    monkeypatch.setattr(settings, "s3_bucket", "test_bucket")
    monkeypatch.setattr(settings, "s3_endpoint", "https://storage.yandexcloud.net")

    from app.services import storage

    monkeypatch.setattr(storage, "get_storage", lambda: FakeStorage())

    r = await c.post(f"/api/projects/{pid}/node-bg?node_key=split_1", files=files)
    assert r.status_code == 200, r.text
    assert any("projects" in k and "split_1.png" in k for k in uploaded)
    sidecar = proj_dir / "node_backgrounds" / "split_1.json"
    assert sidecar.is_file()



@pytest.mark.asyncio
async def test_project_cover_not_found(cover_client):
    c, pid, _, _ = cover_client
    r = await c.get(f"/api/artifacts/project-cover/{pid}")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_project_cover_with_scene_image(cover_client):
    c, pid, proj_dir, factory = cover_client

    # Create dummy image file
    images_dir = proj_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    img_file = images_dir / "shot_01.png"
    img_file.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDRfakeshot")

    async with factory() as session:
        session.add(
            Artifact(
                project_id=pid,
                kind=ArtifactKind.scene_image,
                uuid=uuid.uuid4().hex,
                path=str(img_file),
            )
        )
        await session.commit()

    r = await c.get(f"/api/artifacts/project-cover/{pid}")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("image/")
    assert len(r.content) > 0
