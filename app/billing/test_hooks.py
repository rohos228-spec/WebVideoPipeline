"""Тестовые хуки (только VIA_TEST_HOOKS=1): имитация генерации и очереди."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.billing import charge_hook, limits
from app.billing.identity import CurrentUser

router = APIRouter(prefix="/_test", tags=["test-hooks"])


class GenIn(BaseModel):
    model: str = "video-basic"
    kind: str = "video"
    fail: bool = False


class EnqIn(BaseModel):
    count: int = 1
    kind: str = "video"


@router.post("/generation/run")
async def run_generation(body: GenIn, user: CurrentUser) -> dict:
    job = f"test-{uuid.uuid4().hex}"
    from app.services.model_health import record_run

    try:
        with charge_hook.charged(user["id"], job, model=body.model, kind=body.kind) as cost:
            if body.fail:
                raise RuntimeError("generation failed")
    except charge_hook.InsufficientCredits as e:
        raise HTTPException(402, str(e)) from e
    except RuntimeError as e:
        record_run(model=body.model, ok=False, call_type=body.kind or "", error="simulated failure")
        raise HTTPException(502, "Ошибка генерации, кредиты возвращены") from e
    record_run(model=body.model, ok=True, call_type=body.kind or "")
    return {"ok": True, "job_id": job, "charged": cost}


@router.post("/queue/enqueue")
async def enqueue(body: EnqIn, user: CurrentUser) -> dict:
    uid = user["id"]
    out = [limits.try_acquire(uid, body.kind, f"q-{uuid.uuid4().hex}") for _ in range(max(0, body.count))]
    return {"statuses": out}


@router.get("/queue/state")
async def queue_state(user: CurrentUser, kind: str = "video") -> dict:
    return limits.state(user["id"], kind)
