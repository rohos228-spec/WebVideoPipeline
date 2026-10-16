"""GET /api/models/health — live-статистика моделей для дашборда профиля."""

from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.services import model_health

router = APIRouter(tags=["model-health"])


@router.get("/models/health")
@router.get("/model-health")
def models_health() -> dict:
    return {"models": model_health.get_health()}


class _RunIn(BaseModel):
    model: str
    fail: bool = False
    user_id: str | None = None


def _hooks_enabled() -> bool:
    return os.environ.get("VIA_TEST_HOOKS", "").strip() in ("1", "true", "yes")


@router.post("/_test/generation/run", include_in_schema=False)
def test_generation_run(body: _RunIn) -> dict:
    """Тестовый хук (только VIA_TEST_HOOKS=1): имитация запуска генерации без провайдера."""
    if not _hooks_enabled():
        raise HTTPException(status_code=404)
    model_health.record_run(
        model=body.model,
        ok=not body.fail,
        call_type="test",
        error="simulated failure" if body.fail else "",
    )
    return {"ok": not body.fail, "model": body.model}