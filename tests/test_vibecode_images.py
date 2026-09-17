"""Картинки через vibecode.moe — транспорт generate/download, всё на моках."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from app.bots import vibecode_images as vi
from app.settings import settings

_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 4000

_REAL_ASYNC_CLIENT = httpx.AsyncClient


@pytest.fixture(autouse=True)
def _key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "vibecode_api_key", "test-key")
    monkeypatch.setattr(settings, "vibecode_base_url", "https://vibecode.moe/v1")


def _mock_client(routes, captured: list[httpx.Request]):
    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        for path, resp in routes:
            if path in str(request.url):
                return resp() if callable(resp) else resp
        return httpx.Response(404, text="nope")

    class _Factory:
        def __init__(self, *_a, **_k) -> None:
            self._c = _REAL_ASYNC_CLIENT(transport=httpx.MockTransport(handler))

        async def __aenter__(self) -> httpx.AsyncClient:
            return await self._c.__aenter__()

        async def __aexit__(self, *a) -> None:
            await self._c.__aexit__(*a)

    return _Factory


def _ok_generate() -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "created": 1,
            "data": [{"url": "https://vibecode.moe/v1/images/file/abc.png"}],
        },
    )


def _ok_bytes() -> httpx.Response:
    return httpx.Response(200, content=_PNG)


@pytest.mark.asyncio
async def test_generate_image_ok(tmp_path: Path, monkeypatch) -> None:
    captured: list[httpx.Request] = []
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        _mock_client(
            [("/images/generations", _ok_generate), ("/images/file/", _ok_bytes)],
            captured,
        ),
    )
    out = tmp_path / "a.png"
    res = await vi.generate_image("red square", out, model_slug="gpt-image-2.5")
    assert out.read_bytes() == _PNG
    assert res.raw_url == "https://vibecode.moe/v1/images/file/abc.png"
    assert res.gen_id is None
    assert any("/images/generations" in str(r.url) for r in captured)


@pytest.mark.asyncio
async def test_generate_image_http_error(tmp_path: Path, monkeypatch) -> None:
    captured: list[httpx.Request] = []
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        _mock_client([("/images/generations", lambda: httpx.Response(401, text="no"))], captured),
    )
    with pytest.raises(RuntimeError, match="HTTP 401"):
        await vi.generate_image("x", tmp_path / "a.png")


@pytest.mark.asyncio
async def test_generate_image_no_url(tmp_path: Path, monkeypatch) -> None:
    captured: list[httpx.Request] = []
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        _mock_client([("/images/generations", lambda: httpx.Response(200, json={"data": []}))], captured),
    )
    with pytest.raises(RuntimeError, match="нет URL"):
        await vi.generate_image("x", tmp_path / "a.png")


def test_not_configured_without_key(monkeypatch) -> None:
    monkeypatch.setattr(settings, "vibecode_api_key", "")
    assert vi.vibecode_images_configured() is False
    with pytest.raises(ValueError, match="VIBECODE_API_KEY"):
        vi._headers()


def test_configured_with_key() -> None:
    assert vi.vibecode_images_configured() is True
