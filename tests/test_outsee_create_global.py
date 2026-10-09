"""Глобальные settings Outsee Create (не project-scoped)."""

from __future__ import annotations

from pathlib import Path

from app.web.routers import outsee_create as oc


def test_default_settings_keys(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(oc.settings, "data_dir", tmp_path)
    s = oc._load_settings()
    assert s["media_type"] == "image"
    assert s["image_slug"] == "gpt-image-2"
    assert s["video_slug"] == "sora-2"
    assert s["audio_slug"] == "kie:suno-music"
    assert "prompt" in s


def test_settings_roundtrip(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(oc.settings, "data_dir", tmp_path)
    saved = oc._save_settings(
        {
            "media_type": "video",
            "video_slug": "kling-2-6",
            "aspect": "9:16",
            "duration": "10",
        }
    )
    assert saved["media_type"] == "video"
    assert saved["video_slug"] == "kling-2-6"
    assert saved["aspect"] == "9:16"
    again = oc._load_settings()
    assert again["video_slug"] == "kling-2-6"
    assert again["duration"] == "10"
    assert (tmp_path / "outsee_create_settings.json").is_file()


def test_download_media_local_video(tmp_path: Path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    monkeypatch.setattr(oc.settings, "data_dir", tmp_path)
    video_file = tmp_path / "sample_video.mp4"
    video_content = b"\x00\x00\x00\x20ftypisom" + b"dummy video payload"
    video_file.write_bytes(video_content)

    app = FastAPI()
    app.include_router(oc.router, prefix="/api")
    client = TestClient(app)

    r = client.get(
        "/api/outsee-create/download",
        params={"path": str(video_file), "format": "mp4", "filename": "видео_1"},
    )
    assert r.status_code == 200
    assert r.headers["content-type"] == "video/mp4"
    assert "attachment" in r.headers["content-disposition"]
    assert "filename=" in r.headers["content-disposition"]
    assert r.content == video_content


def test_download_media_via_url_files_param(tmp_path: Path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    monkeypatch.setattr(oc.settings, "data_dir", tmp_path)
    video_file = tmp_path / "stream_video.mp4"
    video_content = b"\x00\x00\x00\x20ftypisom" + b"file param content"
    video_file.write_bytes(video_content)

    app = FastAPI()
    app.include_router(oc.router, prefix="/api")
    client = TestClient(app)

    r = client.get(
        "/api/outsee-create/download",
        params={"url": f"/api/files?path={video_file}", "format": "mp4", "filename": "sample"},
    )
    assert r.status_code == 200
    assert r.headers["content-type"] == "video/mp4"
    assert r.content == video_content

