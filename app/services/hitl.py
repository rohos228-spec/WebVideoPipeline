"""HITL-гейты: создание запроса на подтверждение в Web Studio и ожидание решения пользователя."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import session_scope
from app.models import HITLDecision, HITLKind, HITLRequest, Project
from app.services.event_bus import publish_hitl_event
from app.settings import settings


async def _auto_approve(session: AsyncSession, req: HITLRequest) -> None:
    """HITL_AUTO_APPROVE=1 — решить карточку сразу, как это сделал бы человек."""
    if not getattr(settings, "hitl_auto_approve", False):
        return
    from app.services.hitl_apply import apply_hitl_side_effects

    req.decision = HITLDecision.approved
    req.decided_at = datetime.now(UTC).replace(tzinfo=None)
    await session.flush()
    await apply_hitl_side_effects(session, req, HITLDecision.approved)
    logger.info(
        "[#{}] hitl_auto_approve: {} #{} одобрена автоматически",
        req.project_id,
        req.kind.value if hasattr(req.kind, "value") else req.kind,
        req.id,
    )


async def create_hitl(
    session: AsyncSession,
    project: Project,
    kind: HITLKind,
    payload: dict | None = None,
    frame_id: int | None = None,
) -> HITLRequest:
    req = HITLRequest(
        project_id=project.id,
        frame_id=frame_id,
        kind=kind,
        payload=payload or {},
    )
    session.add(req)
    await session.flush()
    await _auto_approve(session, req)
    return req


async def send_hitl_text(
    bot: Any,
    session: AsyncSession,
    project: Project,
    kind: HITLKind,
    title: str,
    text: str,
    payload: dict | None = None,
    frame_id: int | None = None,
) -> HITLRequest:
    req = await create_hitl(session, project, kind, payload=payload, frame_id=frame_id)
    await publish_hitl_event(
        project.id,
        req.id,
        event_type="hitl_pending",
        payload={
            "kind": kind.value if hasattr(kind, "value") else str(kind),
            "title": title,
            "text": text[:500],
            "frame_id": frame_id,
        },
    )
    return req


async def send_hitl_photo(
    bot: Any,
    session: AsyncSession,
    project: Project,
    kind: HITLKind,
    photo_path: str,
    caption: str,
    payload: dict | None = None,
    frame_id: int | None = None,
    allow_edit: bool = False,
) -> HITLRequest:
    payload = dict(payload or {})
    payload.setdefault("photo_path", photo_path)
    req = await create_hitl(session, project, kind, payload=payload, frame_id=frame_id)
    await publish_hitl_event(
        project.id,
        req.id,
        event_type="hitl_pending",
        payload={
            "kind": kind.value if hasattr(kind, "value") else str(kind),
            "photo_path": photo_path,
            "caption": caption[:500],
            "frame_id": frame_id,
        },
    )
    return req


async def send_hitl_video(
    bot: Any,
    session: AsyncSession,
    project: Project,
    kind: HITLKind,
    video_path: str,
    caption: str,
    payload: dict | None = None,
    frame_id: int | None = None,
) -> HITLRequest:
    req = await create_hitl(session, project, kind, payload=payload, frame_id=frame_id)
    await publish_hitl_event(
        project.id,
        req.id,
        event_type="hitl_pending",
        payload={
            "kind": kind.value if hasattr(kind, "value") else str(kind),
            "video_path": video_path,
            "caption": caption[:500],
            "frame_id": frame_id,
        },
    )
    return req


async def wait_for_decision(hitl_id: int, *, poll_seconds: float = 2.0) -> HITLDecision:
    """Блокирует текущую корутину, пока HITL не будет принят/отклонён/regen в Studio."""
    logger.info("waiting for HITL {}", hitl_id)
    while True:
        async with session_scope() as s:
            req = (await s.execute(select(HITLRequest).where(HITLRequest.id == hitl_id))).scalar_one_or_none()
            if req is None:
                raise RuntimeError(f"HITL #{hitl_id} исчез из БД")
            if req.decision is not HITLDecision.pending:
                logger.info("HITL {} decided: {}", hitl_id, req.decision.value)
                return req.decision
        await asyncio.sleep(poll_seconds)
