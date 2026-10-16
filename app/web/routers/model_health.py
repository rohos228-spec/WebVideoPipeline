"""GET /api/models/health — live-статистика моделей для дашборда профиля."""

from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.services import model_health

router = APIRouter(tags=["model-health"])


@router.get("/models/health")
@router.get("/model-health")
def models_health(window: str = "24h") -> dict:
    return model_health.get_health(model_health.parse_window(window))


class _RunIn(BaseModel):
    model: str
    fail: bool = False
    user_id: str | None = None


def _hooks_enabled() -> bool:
    if os.environ.get("VIA_TEST_HOOKS", "").strip() not in ("1", "true", "yes"):
        return False
    # Billing test hooks (agent E) own this path when present.
    try:
        import app.billing.test_hooks  # noqa: F401
    except Exception:  # noqa: BLE001
        return True
    return False


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