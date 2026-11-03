"""GET /api/projects/{id}/publications — публикации в модели заказчика, только чтение.

Шаг публикации пишет итог в meta.published_platforms; ручка показывает его по
площадкам и даёт sha256 файла и подписи для карточки publish.send.
"""

from __future__ import annotations

import hashlib
import uuid

import httpx
from fastapi import FastAPI

from app.models import Artifact, ArtifactKind, Project, ProjectStatus
from app.web.deps import get_session
from app.web.routers import publications as pub_router


def _client() -> httpx.AsyncClient:
    import app.db as app_db

    async def _session():
        async with app_db.SessionLocal() as s:
            yield s

    app = FastAPI()
    app.include_router(pub_router.router, prefix="/api")
    app.dependency_overrides[get_session] = _session
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


async def _project(meta: dict, video: bytes | None, tmp_path) -> int:
    import app.db as app_db

    async with app_db.SessionLocal() as s:
        p = Project(
            slug=f"pub-{uuid.uuid4().hex[:8]}",
            topic="кофе на рассвете",
            status=ProjectStatus.assembled,
            meta=meta,
        )
        s.add(p)
        await s.flush()
        if video is not None:
            f = tmp_path / "final.mp4"
            f.write_bytes(video)
            s.add(
                Artifact(project_id=p.id, kind=ArtifactKind.final_video, uuid=uuid.uuid4().hex, path=str(f))
            )
        await s.commit()
        return int(p.id)


async def test_publications_show_platform_state_from_project_meta(tmp_path) -> None:
    from app.services.action_layer.publish_action import registry_platforms

    plats = registry_platforms()
    pid = await _project({"published_platforms": {plats[0]: "https://example.test/p/1"}}, b"video", tmp_path)
    async with _client() as c:
        r = await c.get(f"/api/projects/{pid}/publications")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["sha256"] == hashlib.sha256(b"video").hexdigest()
    assert body["caption"] == "кофе на рассвете"
    assert body["caption_sha256"] == hashlib.sha256("кофе на рассвете".encode()).hexdigest()
    assert body["publish_enabled"] is False
    assert [p["platform"] for p in body["platforms"]] == plats
    first = body["platforms"][0]
    assert first["published"] is True and first["post_url"] == "https://example.test/p/1"
    assert all(not p["published"] and p["post_url"] is None for p in body["platforms"][1:])
    assert all(p["approved"] is False for p in body["platforms"])
    assert len(body["pending"]) == len(plats) - 1
    assert body["video"]["url"].startswith("/api/artifacts/")


async def test_publications_without_final_video_and_missing_project(tmp_path) -> None:
    pid = await _project({}, None, tmp_path)
    async with _client() as c:
        r = await c.get(f"/api/projects/{pid}/publications")
        missing = await c.get("/api/projects/999999/publications")
    assert r.status_code == 200
    assert r.json()["sha256"] is None and r.json()["video"] is None
    assert missing.status_code == 404


def test_chat_publish_always_needs_the_human_button() -> None:
    from app.services import project_chat_guard as guard

    act = {"run_step": "publish"}
    assert guard.needs_confirm(act, auto=True) is True
    assert guard.needs_confirm({"run_step": "img"}, auto=True) is False
    title = guard.action_title(act, {})
    assert "все площадки" in title and "Отозвать из студии нельзя" in title


async def test_chat_publish_refused_when_autopublish_is_off_or_no_video(tmp_path, monkeypatch) -> None:
    import app.db as app_db
    from app.services import project_chat_guard as guard
    from app.settings import settings

    pid = await _project({}, b"v", tmp_path)
    async with app_db.SessionLocal() as s:
        p = await s.get(Project, pid)
        monkeypatch.setattr(settings, "social_publish_enabled", False)
        assert "автопубликация выключена" in await guard.publish_refusal(s, p)
        monkeypatch.setattr(settings, "social_publish_enabled", True)
        assert await guard.publish_refusal(s, p) is None
        p.status = ProjectStatus.new
        assert "собранный ролик" in await guard.publish_refusal(s, p)
    empty = await _project({}, None, tmp_path)
    async with app_db.SessionLocal() as s:
        assert "нет финального видео" in await guard.publish_refusal(s, await s.get(Project, empty))
