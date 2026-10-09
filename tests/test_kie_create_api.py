"""REST kie-create: каталог, estimate, валидация, generate через мок kie_http."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.web.routers import kie_create


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(kie_create.router, prefix="/api")
    return app


def test_catalog_endpoint_lists_models() -> None:
    c = TestClient(_app())
    r = c.get("/api/kie-create/catalog")
    assert r.status_code == 200
    data = r.json()
    assert data["credit_usd"] == 0.005
    assert len(data["models"]) >= 50
    veo = next(m for m in data["models"] if m["id"] == "veo-3-1")
    assert any(f["name"] == "resolution" for f in veo["fields"])
    assert veo["pricing"]["rules"]
    assert isinstance(data["configured"], bool)


def test_estimate_endpoint_dynamic() -> None:
    c = TestClient(_app())
    r = c.post(
        "/api/kie-create/estimate",
        json={"model_id": "seedance-2-5", "values": {"resolution": "1080p", "duration": 10}},
    )
    assert r.status_code == 200
    assert r.json()["credits"] == 114 * 10
    r2 = c.post(
        "/api/kie-create/estimate",
        json={"model_id": "seedance-2-5", "values": {"resolution": "480p", "duration": 5}},
    )
    assert r2.json()["credits"] == 28 * 5
    assert r.json()["usd"] > r2.json()["usd"]


def test_generate_validation_422() -> None:
    c = TestClient(_app())
    r = c.post(
        "/api/kie-create/generate",
        json={"model_id": "seedance-2-5", "values": {"resolution": "999p"}},
    )
    assert r.status_code == 422


def test_generate_unknown_model_404() -> None:
    c = TestClient(_app())
    r = c.post("/api/kie-create/generate", json={"model_id": "nope", "values": {}})
    assert r.status_code == 404


def test_generate_enqueues_job(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import create_jobs as cj
    from app.services import generation_storage as gs

    monkeypatch.setattr(gs.settings, "data_dir", tmp_path)
    monkeypatch.setattr(kie_create.kie_http, "kie_configured", lambda: True)
    cj._JOBS.clear()
    cj._SEMS.clear()
    cj._SEM_SIZES.clear()
    cj._RECENT_FP.clear()

    async def fake_run(spec, payload, out_path: Path):
        assert payload["model"] == "bytedance/seedance-2-5"
        assert payload["input"]["resolution"] == "720p"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(b"\x00" * 64)
        from app.bots.outsee import GenerationResult

        return GenerationResult(file_path=out_path, gen_id="task-1", raw_url=None)

    monkeypatch.setattr(kie_create.kie_http, "run_generation", fake_run)

    c = TestClient(_app())
    r = c.post(
        "/api/kie-create/generate",
        json={
            "model_id": "seedance-2-5",
            "values": {"prompt": "тестовый ролик", "resolution": "720p", "duration": 5},
        },
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["job"]["status"] in ("queued", "processing", "done")
    assert data["estimate"]["credits"] == 63 * 5
    assert data["estimate"]["usd"] == round(63 * 5 * 0.005, 4)
    job_id = data["job"]["job_id"]

    # фоновая задача добегает моком → файл появляется
    for _ in range(50):
        jr = c.get(f"/api/kie-create/jobs/{job_id}")
        if jr.json()["status"] in ("done", "failed"):
            break
        import time

        time.sleep(0.1)
    jr = c.get(f"/api/kie-create/jobs/{job_id}").json()
    assert jr["status"] == "done", jr.get("error")
    assert Path(jr["path"]).is_file()


def test_upload_requires_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(kie_create.kie_http, "kie_configured", lambda: False)
    c = TestClient(_app())
    r = c.post(
        "/api/kie-create/upload",
        files={"file": ("a.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 32, "image/png")},
    )
    assert r.status_code == 503


def test_credits_not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_none():
        return None

    monkeypatch.setattr(kie_create.kie_http, "get_credits", fake_none)
    monkeypatch.setattr(kie_create.kie_http, "kie_configured", lambda: False)
    c = TestClient(_app())
    r = c.get("/api/kie-create/credits")
    assert r.status_code == 200
    data = r.json()
    assert data["configured"] is False
    assert data["credits"] is None and data["usd"] is None
    # SR-3: отпечатка ключа в ответе нет (убрано при порте).


@pytest.mark.asyncio
async def test_kie_http_extract_urls_nested() -> None:
    from app.bots.kie_http import _extract_urls

    data = {
        "response": {
            "sunoData": [
                {"audioUrl": "https://x/a.mp3", "imageUrl": "https://x/a.png"},
                {"audioUrl": "https://x/b.mp3"},
            ]
        }
    }
    urls = _extract_urls(data)
    assert "https://x/a.mp3" in urls and "https://x/b.mp3" in urls


@pytest.mark.asyncio
async def test_kie_http_prefers_longest_suno_mp3_not_cover() -> None:
    from app.bots.kie_http import _result_media_urls

    data = {
        "status": "SUCCESS",
        "response": {
            "sunoData": [
                {
                    "audioUrl": "https://x/short.mp3",
                    "imageUrl": "https://x/cover.jpeg",
                    "streamAudioUrl": "https://musicfile.kie.ai/stream-no-ext",
                    "duration": 2.0,
                },
                {
                    "audioUrl": "https://x/long.mp3",
                    "imageUrl": "https://x/cover2.jpeg",
                    "duration": 8.04,
                },
            ]
        },
    }
    urls = _result_media_urls(data)
    assert urls[0] == "https://x/long.mp3"
    assert all(_u.endswith(".mp3") for _u in urls)
    assert "https://x/cover.jpeg" not in urls


def test_kie_http_task_state_mapping() -> None:
    from app.bots.kie_http import _task_state

    assert _task_state("jobs", {"state": "success"}) == "success"
    assert _task_state("jobs", {"state": "fail"}) == "fail"
    assert _task_state("jobs", {"state": "generating"}) == "pending"
    assert _task_state("veo", {"successFlag": 1}) == "success"
    assert _task_state("veo", {"successFlag": 0}) == "pending"
    assert _task_state("suno", {"status": "SUCCESS"}) == "success"
    assert _task_state("suno", {"status": "PENDING"}) == "pending"
    assert _task_state("suno", {"status": "FAILED"}) == "fail"


def test_generate_auto_uploads_data_urls(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import create_jobs as cj
    from app.services import generation_storage as gs

    monkeypatch.setattr(gs.settings, "data_dir", tmp_path)
    monkeypatch.setattr(kie_create.kie_http, "kie_configured", lambda: True)
    cj._JOBS.clear()
    cj._SEMS.clear()
    cj._SEM_SIZES.clear()
    cj._RECENT_FP.clear()

    uploaded_files: list[str] = []

    async def fake_upload_file(content: bytes, filename: str, **kw):
        uploaded_files.append(filename)
        return f"https://tempfile.redpandaai.co/{filename}"

    monkeypatch.setattr(kie_create.kie_http, "upload_file", fake_upload_file)

    async def fake_run(spec, payload, out_path: Path):
        assert payload["input"]["first_frame_url"] == f"https://tempfile.redpandaai.co/{uploaded_files[0]}"
        assert payload["input"]["last_frame_url"] == f"https://tempfile.redpandaai.co/{uploaded_files[1]}"
        assert payload["input"]["reference_image_urls"] == [f"https://tempfile.redpandaai.co/{uploaded_files[2]}"]
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(b"\x00" * 64)
        from app.bots.outsee import GenerationResult

        return GenerationResult(file_path=out_path, gen_id="task-frames-1", raw_url=None)

    monkeypatch.setattr(kie_create.kie_http, "run_generation", fake_run)

    dummy_png_data_url = (
        "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
    )

    c = TestClient(_app())
    r = c.post(
        "/api/kie-create/generate",
        json={
            "model_id": "seedance-2-5",
            "values": {
                "prompt": "видео с кадрами",
                "resolution": "720p",
                "duration": 5,
                "first_frame_url": dummy_png_data_url,
                "last_frame_url": [dummy_png_data_url],
                "reference_image_urls": [dummy_png_data_url],
            },
        },
    )
    assert r.status_code == 200, r.text
    assert len(uploaded_files) == 3


@pytest.mark.asyncio
async def test_upload_file_uses_multipart_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.bots import kie_http

    monkeypatch.setattr(kie_http, "kie_api_key", lambda: "test-kie-key")

    captured_headers: dict[str, str] = {}
    captured_files: Any = None

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        async def post(self, url: str, *, headers: dict[str, str], files: Any, data: Any):
            nonlocal captured_headers, captured_files
            captured_headers = dict(headers)
            captured_files = files

            class FakeResponse:
                status_code = 200

                def json(self):
                    return {"code": 200, "data": {"downloadUrl": "https://tempfile.redpandaai.co/ok.png"}}

            return FakeResponse()

    monkeypatch.setattr(kie_http.httpx, "AsyncClient", lambda **kw: FakeClient())

    dl = await kie_http.upload_file(b"dummy image bytes", "ok.png")
    assert dl == "https://tempfile.redpandaai.co/ok.png"
    assert "Content-Type" not in captured_headers
    assert captured_headers.get("Authorization") == "Bearer test-kie-key"
    assert "file" in captured_files

