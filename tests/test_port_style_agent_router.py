"""Ручки /api/projects/{id}/style-agent* (роутер перенесён из video-pipeline).

Формы ответов сверены с web/src/lib/api.ts (StyleAgentSnapshot,
StyleAgentEstimate, uploadStyleRef …) и style-extract-panel.tsx. Модель не
зовётся: фоновый job подменён, оценка цены считается без вызова.
"""

from __future__ import annotations

import json

import httpx
from fastapi import FastAPI

from app.models import Project, ProjectStatus
from app.services.style_agent import job as sa_job
from app.web.deps import get_session
from app.web.routers import style_agent as style_agent_router


def _app() -> FastAPI:
    import app.db as app_db

    async def _session():
        async with app_db.SessionLocal() as s:
            yield s

    app = FastAPI()
    app.include_router(style_agent_router.router, prefix="/api")
    app.dependency_overrides[get_session] = _session
    return app


async def _project() -> Project:
    import app.db as app_db

    async with app_db.SessionLocal() as s:
        p = Project(slug="style", topic="t", status=ProjectStatus.new)
        s.add(p)
        await s.commit()
        return p


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=_app()), base_url="http://t")


async def test_snapshot_shape_matches_frontend() -> None:
    p = await _project()
    async with _client() as c:
        r = await c.get(f"/api/projects/{p.id}/style-agent?node_key=style_extract")
        assert r.status_code == 200
        snap = r.json()
        assert set(snap) >= {"settings", "job", "videos", "passport", "report", "apply"}
        for key in (
            "max_llm_calls",
            "min_support",
            "iqr_threshold",
            "max_sheets_per_video",
            "agents",
            "skip_llm",
        ):
            assert key in snap["settings"]
        assert snap["job"]["status"] == "idle"
        assert snap["videos"] == []
        assert set(snap["passport"]) >= {"hasDraft", "hasApproved", "style_id", "label", "problems"}
        assert (await c.get("/api/projects/9999/style-agent")).status_code == 404


async def test_patch_settings_valid_and_invalid() -> None:
    p = await _project()
    async with _client() as c:
        r = await c.patch(f"/api/projects/{p.id}/style-agent", json={"label": "Нуар", "min_support": 5})
        assert r.status_code == 200
        st = r.json()["settings"]
        assert st["label"] == "Нуар"
        assert st["min_support"] == 1.0
        r = await c.patch(f"/api/projects/{p.id}/style-agent", json={"nope": 1})
        assert r.status_code == 400
        snap = (await c.get(f"/api/projects/{p.id}/style-agent")).json()
        assert snap["settings"]["label"] == "Нуар"


async def test_upload_list_delete_ref() -> None:
    p = await _project()
    async with _client() as c:
        r = await c.post(
            f"/api/projects/{p.id}/style-agent/upload",
            files={"file": ("ref one.mp4", b"\x00\x01video", "video/mp4")},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["size"] == 7
        assert body["videos"] == [body["name"]]
        bad = await c.post(
            f"/api/projects/{p.id}/style-agent/upload",
            files={"file": ("x.txt", b"hi", "text/plain")},
        )
        assert bad.status_code == 400
        snap = (await c.get(f"/api/projects/{p.id}/style-agent")).json()
        assert [v["name"] for v in snap["videos"]] == [body["name"]]
        assert snap["videos"][0]["uploaded"] is True
        assert snap["videos"][0]["analyzed"] is False
        r = await c.delete(f"/api/projects/{p.id}/style-agent/refs/{body['name']}")
        assert r.status_code == 200
        assert r.json()["videos"] == []
        assert (await c.delete(f"/api/projects/{p.id}/style-agent/refs/{body['name']}")).status_code == 404


async def test_estimate_does_not_call_model() -> None:
    p = await _project()
    async with _client() as c:
        await c.post(
            f"/api/projects/{p.id}/style-agent/upload",
            files={"file": ("a.mp4", b"vid", "video/mp4")},
        )
        r = await c.post(f"/api/projects/{p.id}/style-agent/estimate?node_key=style_extract", json={})
        assert r.status_code == 200, r.text
        est = r.json()
        assert est["videos"] == ["a.mp4"]
        for key in ("n_videos", "max_calls", "agents", "planned_total", "fits"):
            assert key in est["plan"]
        assert "settings" in est and "cost" in est


async def test_run_requires_videos_then_spawns_job(monkeypatch) -> None:
    p = await _project()
    spawned: list[tuple] = []
    monkeypatch.setattr(
        sa_job,
        "spawn_style_extract_job",
        lambda pid, *, node_key, overrides: spawned.append((pid, node_key, overrides)),
    )
    async with _client() as c:
        r = await c.post(f"/api/projects/{p.id}/style-agent/run", json={})
        assert r.status_code == 400
        await c.post(
            f"/api/projects/{p.id}/style-agent/upload",
            files={"file": ("a.mp4", b"vid", "video/mp4")},
        )
        r = await c.post(
            f"/api/projects/{p.id}/style-agent/run?node_key=style_extract",
            json={"max_llm_calls": 7, "resume": True},
        )
        assert r.status_code == 200, r.text
        assert r.json()["started"] is True
        assert "plan" in r.json()["estimate"]
        assert spawned and spawned[0][0] == p.id and spawned[0][1] == "style_extract"
        assert spawned[0][2]["resume"] is True
        snap = (await c.get(f"/api/projects/{p.id}/style-agent")).json()
        assert snap["settings"]["max_llm_calls"] == 7
        assert "resume" not in snap["settings"] or snap["settings"]["resume"] is False
        r = await c.post(f"/api/projects/{p.id}/style-agent/stop")
        assert r.json() == {"stopped": False}


async def test_passport_draft_approve_and_report(monkeypatch) -> None:
    p = await _project()
    async with _client() as c:
        assert (await c.get(f"/api/projects/{p.id}/style-agent/passport")).status_code == 404
        assert (
            await c.put(f"/api/projects/{p.id}/style-agent/passport", json={"passport": 3})
        ).status_code == 400
        r = await c.put(f"/api/projects/{p.id}/style-agent/passport", json={"passport": {"style_id": "x"}})
        assert r.status_code == 400
        assert r.json()["detail"]["problems"]

        rep = await c.get(f"/api/projects/{p.id}/style-agent/report")
        assert rep.status_code == 200
        assert rep.json() == {"report": "", "variance_report": "", "apply_report": None}
        md = await c.get(f"/api/projects/{p.id}/style-agent/report?fmt=md")
        assert md.status_code == 200
        assert md.headers["content-type"].startswith("text/markdown")

        assert (await c.post(f"/api/projects/{p.id}/style-agent/approve", json={})).status_code == 400

        # Валидный черновик: approve без материализации отдаёт отчёт и снимок.
        passport = {"style_id": "noir", "created_at": "2026-11-01T00:00:00Z"}
        monkeypatch.setattr(sa_job.schema, "validate_passport", lambda _p: [])
        monkeypatch.setattr(style_agent_router.sa_schema, "validate_passport", lambda _p: [])
        r = await c.put(f"/api/projects/{p.id}/style-agent/passport", json={"passport": passport})
        assert r.status_code == 200
        assert r.json() == {"ok": True, "problems": []}
        got = (await c.get(f"/api/projects/{p.id}/style-agent/passport")).json()
        assert got["passport"]["style_id"] == "noir"
        r = await c.post(f"/api/projects/{p.id}/style-agent/approve", json={"materialize": False})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["report"]["materialized"] is False
        assert body["snapshot"]["passport"]["hasApproved"] is True


async def test_sheet_is_confined_to_analysis_dir() -> None:
    p = await _project()
    sheets = sa_job.analysis_dir(p) / "a" / "sheets"
    sheets.mkdir(parents=True)
    (sheets / "s1.png").write_bytes(b"IMG")
    (sa_job.style_dir(p) / "secret.json").write_text(json.dumps({}), encoding="utf-8")
    async with _client() as c:
        r = await c.get(
            f"/api/projects/{p.id}/style-agent/sheet", params={"video": "a.mp4", "name": "s1.png"}
        )
        assert r.status_code == 200
        assert r.content == b"IMG"
        r = await c.get(
            f"/api/projects/{p.id}/style-agent/sheet",
            params={"video": "a.mp4", "name": "../../../secret.json"},
        )
        assert r.status_code == 404
