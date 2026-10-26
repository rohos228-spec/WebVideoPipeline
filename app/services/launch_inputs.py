"""Вспомогательные проверки входных данных для запуска шагов (кадры, раскадровка)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Frame

_FRAME_STEPS = frozenset({
    "image_prompts",
    "images",
    "img",
    "animation_prompts",
    "video",
    "videos",
    "render",
    "upscale",
})


def needs_frames(step_code: str | None) -> bool:
    if not step_code:
        return False
    return step_code in _FRAME_STEPS


def board_frame_count(board: Any) -> int:
    if not isinstance(board, dict):
        return 0
    frames = board.get("frames")
    if isinstance(frames, list):
        return len(frames)
    return 0


def no_frames_text(step_code: str | None) -> str:
    title = step_code or "шага"
    return f"В проекте нет кадров для шага «{title}». Сначала выполните разбивку на кадры."


async def project_frames(project_id: int) -> list[dict[str, Any]]:
    async with SessionLocal() as session:
        stmt = select(Frame).where(Frame.project_id == project_id).order_by(Frame.number)
        res = await session.execute(stmt)
        frames = res.scalars().all()
        return [
            {
                "id": f.id,
                "number": f.number,
                "text": getattr(f, "text", getattr(f, "voiceover_text", "")),
                "image_prompt": f.image_prompt,
                "animation_prompt": f.animation_prompt,
                "status": f.status.value if f.status else None,
            }
            for f in frames
        ]


async def plan_refusal(step_spec: Any, project: Any = None) -> str | None:
    return None
