"""GET /api/models/health — live-статистика моделей для дашборда профиля.

Тестовые хуки уведомлений/отчётов (только VIA_TEST_HOOKS=1). Хук запуска генерации
живёт в app/billing/test_hooks.py (агент E) и пишет статистику через model_health.record_run.
"""

from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException

from app.services import model_health

router = APIRouter(tags=["model-health"])


@router.get("/models/health")
@router.get("/model-health")
def models_health(window: str = "24h") -> dict:
    return model_health.get_health(model_health.parse_window(window))


def _require_hooks() -> None:
    if os.environ.get("VIA_TEST_HOOKS", "").strip() not in ("1", "true", "yes"):
        raise HTTPException(status_code=404)


def _scrub(text: str) -> str:
    try:
        from app.brand import scrub

        return scrub(text)
    except Exception:  # noqa: BLE001
        return model_health.scrub(text)


@router.get("/_test/notify/last", include_in_schema=False)
def test_notify_last() -> dict:
    """Последнее уведомление пользователю (как его увидит UI): последняя ошибка генерации."""
    _require_hooks()
    data = model_health.get_health(24)
    errs = [m for m in data["models"] if m.get("last_error")]
    if not errs:
        return {"text": ""}
    m = max(errs, key=lambda x: x.get("updated_at") or "")
    return {"text": _scrub(f"Ошибка генерации ({m['model']}): {m['last_error']}. Кредиты возвращены.")}


@router.get("/_test/report/last", include_in_schema=False)
def test_report_last() -> dict:
    """Последний отчёт пользователю: сводка здоровья моделей в текстовом виде."""
    _require_hooks()
    data = model_health.get_health(24)
    lines = [f"{m['model']}: успехов {m['success']}, ошибок {m['errors']}" for m in data["models"]]
    return {"report": _scrub("\n".join(lines) or "Запусков нет")}