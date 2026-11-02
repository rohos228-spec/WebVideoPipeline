"""API маршрутизатора генераций `/api/generate`.

Позволяет вызывать генерацию или регистрировать внешние генерации
с автоматической записью `MediaCall`, `Artifact` и привязкой к `tenant_id`.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ArtifactKind
from app.services.generation_gateway import GenerationContext, gateway
from app.services.media_ledger import price_for
from app.services.media_route import (
    KIE_IMAGE_IDS,
    KIE_VIDEO_IDS,
    OUTSEE_IMAGE_IDS,
    OUTSEE_VIDEO_IDS,
    image_provider_for,
    video_provider_for,
)
from app.services.studio_auth import current_identity
from app.web.deps import get_session

router = APIRouter(prefix="/generate", tags=["generation-gateway"])


class ImageGenerateRequest(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=5000)
    model: str = "flux-2-pro"
    project_id: int | None = None
    frame_id: int | None = None
    node_key: str = "adhoc"
    aspect: str = "16:9"
    shot: int = 1


class RecordExternalRequest(BaseModel):
    kind: str = Field(..., description="image|video|audio")
    model: str
    provider: str
    file_path: str
    project_id: int | None = None
    frame_id: int | None = None
    node_key: str = "adhoc"
    shot: int = 1
    units: float = 1.0
    duration_ms: int = 0
    result: str = "ok"
    error_kind: str = ""
    external_id: str = ""


@router.get("/routes")
async def list_routes() -> dict[str, Any]:
    """Список поддерживаемых маршрутов моделей, провайдеров и тарифов."""
    image_models: list[dict[str, Any]] = []
    for m in sorted(list(OUTSEE_IMAGE_IDS) + list(KIE_IMAGE_IDS)):
        prov = image_provider_for(m)
        price = price_for(prov, m)
        image_models.append(
            {
                "model": m,
                "provider": prov,
                "unit": price.unit,
                "per_unit_usd": price.per_unit,
                "per_call_usd": price.per_call,
            }
        )

    video_models: list[dict[str, Any]] = []
    for m in sorted(list(OUTSEE_VIDEO_IDS) + list(KIE_VIDEO_IDS)):
        prov = video_provider_for(m)
        price = price_for(prov, m)
        video_models.append(
            {
                "model": m,
                "provider": prov,
                "unit": price.unit,
                "per_unit_usd": price.per_unit,
                "per_call_usd": price.per_call,
            }
        )

    return {
        "images": image_models,
        "videos": video_models,
    }


@router.post("/image")
async def generate_image_endpoint(
    body: ImageGenerateRequest,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Единый эндпоинт генерации изображения с привязкой к пользователю."""
    identity = current_identity()
    user_id = identity.user_id if identity else None

    context = GenerationContext(
        user_id=user_id,
        tenant_id=user_id,
        project_id=body.project_id,
        frame_id=body.frame_id,
        node_key=body.node_key,
        shot=body.shot,
        source="api_generate_image",
    )

    res = await gateway.generate_image(
        session,
        context,
        prompt=body.prompt,
        model=body.model,
        aspect=body.aspect,
    )
    if not res.success:
        raise HTTPException(
            status_code=502,
            detail={"error": res.error, "error_kind": res.error_kind, "call_id": res.media_call_id},
        )
    return res.to_dict()


@router.post("/record")
async def record_external_endpoint(
    body: RecordExternalRequest,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Атомарная регистрация внешней генерации в MediaCall и Artifact с tenant_id."""
    from pathlib import Path

    identity = current_identity()
    user_id = identity.user_id if identity else None

    context = GenerationContext(
        user_id=user_id,
        tenant_id=user_id,
        project_id=body.project_id,
        frame_id=body.frame_id,
        node_key=body.node_key,
        shot=body.shot,
        source="external_record",
    )

    kind_map = {
        "image": ArtifactKind.scene_image,
        "video": ArtifactKind.scene_video,
        "audio": ArtifactKind.audio,
    }
    art_kind = kind_map.get(body.kind, ArtifactKind.scene_image)

    fp = Path(body.file_path) if body.file_path else None
    art, call = await gateway.record_artifact_and_call(
        session,
        context,
        kind=art_kind,
        file_path=fp,
        provider=body.provider,
        model=body.model,
        units=body.units,
        result=body.result,
        error_kind=body.error_kind,
        duration_ms=body.duration_ms,
        external_id=body.external_id,
    )

    await session.commit()
    return {
        "success": True,
        "artifact_id": art.id if art else None,
        "media_call_id": call.id,
        "tenant_id": call.tenant_id,
        "cost_usd": call.cost_usd,
    }
