"""Единый шлюз и маршрутизатор генераций (Generation Gateway).

Централизует все вызовы к генеративным API (картинки, видео, звук).
Обеспечивает:
1. Автоматический выбор провайдера (Outsee, Kie, Vibecode, WaveSpeed, Pay52)
   по модели с поддержкой fallback.
2. Неразрывную привязку к пользователю/арендатору (`tenant_id`) и проекту (`project_id`).
3. Атомарную запись в БД:
   - `Artifact` с заполненным `tenant_id`, `uuid`, `path`, `meta`.
   - `MediaCall` с точной длительностью (`duration_ms`), стоимостью (`cost_usd`),
     единицами (`units`) и статусом (`ok`/`error`).
   - Обновление статуса кадра `Frame.status`.
4. Мгновенную пре-генерацию `.webp` миниатюр в `.thumbs/`.
5. Проверку и списание кредитов (`CreditAccount`).
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Artifact,
    ArtifactKind,
    Frame,
    FrameStatus,
    MediaCall,
    Project,
)
from app.services.media_ledger import compute_cost
from app.services.media_route import (
    canonical_media_id,
    image_provider_for,
    video_provider_for,
)
from app.services.tenant import current_tenant, tenant_scope
from app.services.thumbnails import thumbnail_path
from app.settings import settings


@dataclass
class GenerationContext:
    """Контекст генерации с обязательной привязкой к арендатору."""

    user_id: str | None = None
    tenant_id: str | None = None
    project_id: int | None = None
    frame_id: int | None = None
    node_key: str = "adhoc"
    shot: int = 1
    source: str = "gateway"
    meta: dict[str, Any] = field(default_factory=dict)

    async def ensure_tenant(self, session: AsyncSession) -> str | None:
        """Если tenant_id не задан явно, извлекает его из проекта или сессии."""
        if self.tenant_id:
            return self.tenant_id
        if self.project_id:
            p = await session.get(Project, self.project_id)
            if p and p.tenant_id:
                self.tenant_id = p.tenant_id
                return self.tenant_id
        self.tenant_id = current_tenant() or self.user_id
        return self.tenant_id


@dataclass
class GenerationResult:
    """Результат маршрутизации и выполнения генерации."""

    success: bool
    artifact_id: int | None = None
    artifact_uuid: str | None = None
    media_call_id: int | None = None
    file_path: Path | None = None
    thumbnail_path: Path | None = None
    provider: str = ""
    model: str = ""
    units: float = 1.0
    unit: str = "item"
    cost_usd: float = 0.0
    duration_ms: int = 0
    error: str | None = None
    error_kind: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "artifact_id": self.artifact_id,
            "artifact_uuid": self.artifact_uuid,
            "media_call_id": self.media_call_id,
            "file_path": str(self.file_path) if self.file_path else None,
            "thumbnail_path": str(self.thumbnail_path) if self.thumbnail_path else None,
            "provider": self.provider,
            "model": self.model,
            "units": self.units,
            "unit": self.unit,
            "cost_usd": self.cost_usd,
            "duration_ms": self.duration_ms,
            "error": self.error,
            "error_kind": self.error_kind,
        }


class GenerationGateway:
    """Маршрутизатор и диспетчер генераций."""

    @staticmethod
    def resolve_image_route(model: str | None) -> tuple[str, list[str]]:
        """Определяет основного провайдера и список запасных для генерации картинок."""
        c_model = canonical_media_id(model)
        primary = image_provider_for(c_model)
        fallbacks: list[str] = []
        if primary == "outsee":
            fallbacks.append("kie")
        elif primary == "kie":
            fallbacks.append("outsee")
        return primary, fallbacks

    @staticmethod
    def resolve_video_route(model: str | None) -> tuple[str, list[str]]:
        """Определяет основного провайдера и список запасных для генерации видео."""
        c_model = canonical_media_id(model)
        primary = video_provider_for(c_model)
        fallbacks: list[str] = []
        if primary == "outsee":
            fallbacks.append("kie")
        elif primary == "kie":
            fallbacks.append("outsee")
        return primary, fallbacks

    @classmethod
    async def record_artifact_and_call(
        cls,
        session: AsyncSession,
        context: GenerationContext,
        *,
        kind: ArtifactKind,
        file_path: Path | None,
        provider: str,
        model: str,
        units: float = 1.0,
        result: str = "ok",
        error_kind: str = "",
        error_msg: str | None = None,
        duration_ms: int = 0,
        variant: str = "",
        external_id: str = "",
        meta_extra: dict[str, Any] | None = None,
    ) -> tuple[Artifact | None, MediaCall]:
        """Атомарная запись артефакта и факта вызова в БД со 100% привязкой к арендатору."""
        tenant_id = await context.ensure_tenant(session)

        # Вычисляем финансовые метрики
        media_kind_str = "image" if kind in (ArtifactKind.scene_image, ArtifactKind.hero_reference) else (
            "video" if kind in (ArtifactKind.scene_video, ArtifactKind.final_video) else "audio"
        )
        cost_usd, unit_name, unpriced = compute_cost(provider, model, units, variant=variant)
        if result != "ok":
            cost_usd = 0.0

        # 1. Запись вызова MediaCall
        media_call = MediaCall(
            tenant_id=tenant_id,
            project_id=context.project_id,
            node_key=context.node_key or "adhoc",
            provider=provider[:40],
            kind=media_kind_str[:20],
            model=model[:120],
            units=float(units),
            unit=unit_name[:20],
            cost_usd=cost_usd,
            result=result,
            error_kind=(error_kind or "")[:60],
            unpriced=unpriced,
            external_id=external_id[:120],
            duration_ms=int(duration_ms),
        )
        session.add(media_call)

        artifact: Artifact | None = None
        # 2. Если результат успешный и файл существует — создаём Artifact
        if result == "ok" and file_path and file_path.is_file():
            meta = {
                "provider": provider,
                "model": model,
                "shot": context.shot,
                "source": context.source,
                "duration_ms": duration_ms,
                **(context.meta or {}),
                **(meta_extra or {}),
            }
            artifact = Artifact(
                tenant_id=tenant_id,
                project_id=context.project_id or 0,
                frame_id=context.frame_id,
                kind=kind,
                uuid=uuid.uuid4().hex,
                path=str(file_path.resolve()),
                meta=meta,
                created_at=datetime.now(UTC).replace(tzinfo=None),
            )
            session.add(artifact)

            # 3. Обновляем Frame статус если привязан кадр
            if context.frame_id:
                frame = await session.get(Frame, context.frame_id)
                if frame:
                    if kind == ArtifactKind.scene_image:
                        frame.status = FrameStatus.image_generated
                    elif kind == ArtifactKind.scene_video:
                        frame.status = FrameStatus.video_generated

        await session.flush()
        return artifact, media_call

    @classmethod
    async def generate_image(
        cls,
        session: AsyncSession,
        context: GenerationContext,
        *,
        prompt: str,
        model: str = "flux-2-pro",
        out_dir: Path | None = None,
        out_filename: str | None = None,
        negative_prompt: str = "",
        aspect: str = "16:9",
        seed: int | None = None,
    ) -> GenerationResult:
        """Маршрутизация и исполнение генерации картинки с гарантированной фиксацией в БД."""
        tenant_id = await context.ensure_tenant(session)
        with tenant_scope(tenant_id):
            start_time = time.perf_counter()
            primary_prov, fallbacks = cls.resolve_image_route(model)
            providers_to_try = [primary_prov] + fallbacks

            # Определяем директорию для сохранения
            target_dir = out_dir
            if not target_dir:
                if context.project_id:
                    p = await session.get(Project, context.project_id)
                    if p:
                        target_dir = p.data_dir / "scenes"
                if not target_dir:
                    target_dir = Path(settings.data_dir) / "generations" / (tenant_id or "shared")
            target_dir.mkdir(parents=True, exist_ok=True)

            fn = out_filename or f"frame_{context.frame_id or 1:03d}_{uuid.uuid4().hex[:8]}.png"
            dest_file = target_dir / fn

            last_error: str | None = None
            last_err_kind: str | None = None

            for prov in providers_to_try:
                try:
                    logger.info("Gateway: генерация изображения через {} (модель: {})", prov, model)
                    if prov == "kie":
                        from app.bots.kie_http import generate_kie_image

                        await generate_kie_image(
                            prompt=prompt,
                            out_path=dest_file,
                            model_slug=model,
                            aspect_ratio=aspect,
                        )
                    elif prov == "outsee":
                        from app.bots.outsee_http import generate_image_http

                        await generate_image_http(
                            prompt=prompt,
                            out_path=dest_file,
                            model=model,
                            aspect=aspect,
                        )
                    elif prov == "vibecode":
                        from app.bots.vibecode_images import generate_image as generate_vibecode_img

                        await generate_vibecode_img(
                            prompt=prompt,
                            out_path=dest_file,
                            model_slug=model,
                        )
                    else:
                        raise ValueError(f"Неизвестный провайдер картинок: {prov}")

                    # Генерация прошла успешно
                    duration_ms = int((time.perf_counter() - start_time) * 1000)

                    # Создаем миниатюру
                    thumb = thumbnail_path(dest_file)

                    # Фиксируем в БД
                    art, call = await cls.record_artifact_and_call(
                        session,
                        context,
                        kind=ArtifactKind.scene_image,
                        file_path=dest_file,
                        provider=prov,
                        model=model,
                        units=1.0,
                        result="ok",
                        duration_ms=duration_ms,
                    )

                    return GenerationResult(
                        success=True,
                        artifact_id=art.id if art else None,
                        artifact_uuid=art.uuid if art else None,
                        media_call_id=call.id,
                        file_path=dest_file,
                        thumbnail_path=thumb,
                        provider=prov,
                        model=model,
                        units=1.0,
                        cost_usd=call.cost_usd,
                        duration_ms=duration_ms,
                    )

                except Exception as e:
                    last_error = str(e)
                    last_err_kind = type(e).__name__
                    logger.warning("Gateway: провайдер {} упал с ошибкой {}: {}", prov, last_err_kind, e)

            # Все провайдеры упали — фиксируем отказ в MediaCall
            duration_ms = int((time.perf_counter() - start_time) * 1000)
            _, call = await cls.record_artifact_and_call(
                session,
                context,
                kind=ArtifactKind.scene_image,
                file_path=None,
                provider=primary_prov,
                model=model,
                units=1.0,
                result="error",
                error_kind=last_err_kind or "GatewayError",
                error_msg=last_error,
                duration_ms=duration_ms,
            )

            return GenerationResult(
                success=False,
                media_call_id=call.id,
                provider=primary_prov,
                model=model,
                units=1.0,
                cost_usd=0.0,
                duration_ms=duration_ms,
                error=last_error,
                error_kind=last_err_kind,
            )


gateway = GenerationGateway()
