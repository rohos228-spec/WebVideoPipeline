"""Тесты для WaveSpeed ElevenLabs v4 клиента и Create API."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.bots import wavespeed_http
from app.bots.outsee import GenerationResult
from app.services import kie_catalog
from app.web.routers import kie_create


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(kie_create.router, prefix="/api")
    return app


def test_wavespeed_catalog_entry() -> None:
    model = kie_catalog.get_model("elevenlabs-v4")
    assert model is not None
    assert model["label"] == "Озвучка ElevenLabs v4"
    assert model["api"] == "wavespeed"
    assert model["result"] == "audio"
    assert any(f["name"] == "text" for f in model["fields"])
    assert any(f["name"] == "voice_id" for f in model["fields"])
    assert any(f["name"] == "stability" for f in model["fields"])
    assert any(f["name"] == "similarity" for f in model["fields"])


def test_wavespeed_estimate() -> None:
    model = kie_catalog.get_model("elevenlabs-v4")
    assert model is not None
    est = kie_catalog.estimate_credits(model, {"text": "A" * 1500})
    assert est["credits"] == 6.0  # 3 * 2 (1500 chars = 2 thousands)
    assert est["usd"] > 0


@pytest.mark.asyncio
async def test_wavespeed_create_prediction(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wavespeed_http, "wavespeed_api_key", lambda: "test-key")
    monkeypatch.setattr(wavespeed_http, "wavespeed_api_base_url", lambda: "https://api.wavespeed.ai")

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"code": 200, "data": {"id": "pred-12345"}}

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = FakeResponse()
        pred_id = await wavespeed_http.create_prediction(
            payload={"text": "Привет", "voice_id": "ymDCYd8puC7gYjxIamPt"}
        )
        assert pred_id == "pred-12345"


@pytest.mark.asyncio
async def test_wavespeed_poll_prediction_completed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wavespeed_http, "wavespeed_api_key", lambda: "test-key")
    monkeypatch.setattr(wavespeed_http, "wavespeed_api_base_url", lambda: "https://api.wavespeed.ai")

    class FakeResponse:
        status_code = 200

        def json(self):
            return {
                "code": 200,
                "data": {
                    "id": "pred-12345",
                    "status": "completed",
                    "outputs": ["https://cdn.example.com/audio.mp3"],
                },
            }

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = FakeResponse()
        data = await wavespeed_http.poll_prediction("pred-12345")
        assert data["status"] == "completed"
        assert data["outputs"] == ["https://cdn.example.com/audio.mp3"]


@pytest.mark.asyncio
async def test_wavespeed_run_generation(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(wavespeed_http, "wavespeed_api_key", lambda: "test-key")

    with patch.object(wavespeed_http, "create_prediction", new_callable=AsyncMock) as mock_create, patch.object(
        wavespeed_http, "poll_prediction", new_callable=AsyncMock
    ) as mock_poll, patch.object(wavespeed_http, "download", new_callable=AsyncMock) as mock_download:
        mock_create.return_value = "pred-abc"
        mock_poll.return_value = {
            "status": "completed",
            "outputs": ["https://cdn.example.com/out.mp3"],
        }
        out_file = tmp_path / "speech.mp3"
        out_file.write_bytes(b"FAKE_MP3_CONTENT")
        mock_download.return_value = out_file

        res = await wavespeed_http.run_generation(
            payload={"text": "Тест", "voice_id": "123"},
            out_path=out_file,
        )
        assert isinstance(res, GenerationResult)
        assert res.gen_id == "pred-abc"
        assert res.file_path == out_file
        assert res.raw_url == "https://cdn.example.com/out.mp3"


def test_catalog_endpoint_reports_wavespeed_configured() -> None:
    c = TestClient(_app())
    r = c.get("/api/kie-create/catalog")
    assert r.status_code == 200
    data = r.json()
    assert "wavespeed_configured" in data
    assert any(m["id"] == "elevenlabs-v4" for m in data["models"])
