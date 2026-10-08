"""Тамбнейлы сетки истории: WebP 320px вместо полных 2K-файлов.

Контракт: `GET /api/files?path=…&thumb=1` отдаёт кэшированную миниатюру
только для картинок и только после тех же проверок доступа, что оригинал.
Не-картинки, битые файлы и промахи кэша — молча оригинал (никаких 500).
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base
from app.settings import settings
from app.web.api import create_app
from app.web.deps import get_session


def _png(path, size=(800, 600), color=(200, 30, 30)):
    from PIL import Image

    img = Image.new("RGB", size, color)
    img.save(path, format="PNG")


def test_thumbnail_is_small_webp(tmp_path, monkeypatch) -> None:
    from app.services import thumbnails as th

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    src = tmp_path / "big.png"
    _png(src)
    out = th.thumbnail_path(src)
    assert out is not None and out.is_file()
    assert out.suffix == ".webp"
    assert out.stat().st_size < src.stat().st_size
    from PIL import Image

    with Image.open(out) as im:
        assert im.size[0] == 320
        assert im.format == "WEBP"
    # повтор — тот же кэш, без перегенерации
    assert th.thumbnail_path(src) == out
    # смена файла — новый ключ (stale невозможен)
    src.write_bytes(src.read_bytes() + b"\x00")
    assert th.thumbnail_path(src) != out


def test_thumbnail_refuses_non_images(tmp_path, monkeypatch) -> None:
    from app.services import thumbnails as th

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    txt = tmp_path / "note.txt"
    txt.write_text("hello")
    assert th.thumbnail_path(txt) is None
    assert th.thumbnail_path(tmp_path / "missing.png") is None


def test_prune_removes_stale(tmp_path, monkeypatch) -> None:
    import os
    import time

    from app.services import thumbnails as th

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    src = tmp_path / "a.png"
    _png(src)
    out = th.thumbnail_path(src)
    assert out is not None
    old = time.time() - th.THUMB_TTL_S - 10
    os.utime(out, (old, old))
    assert th.prune_thumbs() == 1
    assert not out.exists()


@pytest_asyncio.fixture
async def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "allow_unisolated_tenants", True)
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 't.db'}", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _gen():
        async with factory() as s:
            yield s

    app = create_app()
    app.dependency_overrides[get_session] = _gen
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    await engine.dispose()


@pytest.mark.asyncio
async def test_files_thumb_serves_webp(client) -> None:
    src = settings.data_dir / "pic.png"
    _png(src)
    res = await client.get("/api/files", params={"path": str(src), "thumb": 1})
    assert res.status_code == 200
    assert res.headers["content-type"] == "image/webp"
    assert len(res.content) > 100


@pytest.mark.asyncio
async def test_files_thumb_falls_back_to_original(client) -> None:
    txt = settings.data_dir / "note.txt"
    txt.write_text("hello")
    res = await client.get("/api/files", params={"path": str(txt), "thumb": 1})
    assert res.status_code == 200
    assert res.content == b"hello"


@pytest.mark.asyncio
async def test_files_thumb_guards_stand(client) -> None:
    res = await client.get("/api/files", params={"path": str(settings.data_dir / "nope.png"), "thumb": 1})
    assert res.status_code == 404
    res = await client.get("/api/files", params={"path": "/etc/passwd", "thumb": 1})
    assert res.status_code == 400
