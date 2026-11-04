"""Действия media.* ходят в /api/projects/{id}/media-recovery/...: роутер должен быть подключён (без него 404)."""

from __future__ import annotations

from app.web.api import create_app


def test_media_recovery_routes_are_mounted() -> None:
    paths = set(create_app().openapi()["paths"])
    base = "/api/projects/{project_id}/media-recovery"
    for tail in ("/unknown-outcomes", "/release-unknown", "/{node_type}", "/{node_type}/resume-poll"):
        assert base + tail in paths
