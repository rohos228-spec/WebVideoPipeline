"""REST: /api/projects/{id}/frames."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import commit_with_retry
from app.models import Artifact, ArtifactKind, Frame, FrameStatus, Project
from app.services.artifact_recovery import archive_older_frame_clips
from app.services.img_streams import VIDEO_INFLIGHT_ATTR
from app.services.plan_shot2 import SHOT2_STATUS_ATTR
from app.web.deps import get_session
from app.web.schemas import FrameDTO, UpdateFrameRequest

router = APIRouter(prefix="/projects/{project_id}/frames", tags=["frames"])


@router.get("", response_model=list[FrameDTO])
async def list_frames(project_id: int, session: AsyncSession = Depends(get_session)) -> list[Frame]:
    rows = (
        (
            await session.execute(
                select(Frame).where(Frame.project_id == project_id).order_by(Frame.number.asc())
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


@router.get("/{frame_id}", response_model=FrameDTO)
async def get_frame(project_id: int, frame_id: int, session: AsyncSession = Depends(get_session)) -> Frame:
    f = await session.get(Frame, frame_id)
    if f is None or f.project_id != project_id:
        raise HTTPException(status_code=404, detail="frame not found")
    return f


@router.patch("/{frame_id}", response_model=FrameDTO)
async def patch_frame(
    project_id: int,
    frame_id: int,
    payload: UpdateFrameRequest,
    session: AsyncSession = Depends(get_session),
) -> Frame:
    f = await session.get(Frame, frame_id)
    if f is None or f.project_id != project_id:
        raise HTTPException(status_code=404, detail="frame not found")
    data = payload.model_dump(exclude_unset=True)
    if "status" in data and data["status"] is not None:
        try:
            data["status"] = FrameStatus(data["status"])
        except ValueError as e:
            raise HTTPException(status_code=400, detail=f"invalid frame status: {data['status']}") from e
    for k, v in data.items():
        setattr(f, k, v)
    await session.commit()
    await session.refresh(f)
    return f


@router.post("/{frame_id}/regenerate-video")
async def regenerate_frame_video(
    project_id: int,
    frame_id: int,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Сбросить клип кадра, чтобы шаг видео собрал его заново.

    Старые ``clip_NNN_*.mp4`` обоих shot уезжают в ``old/videos/``, запись
    Artifact ``scene_video`` кадра удаляется, флаги генерации
    (``video_gen_skip`` / ``video_inflight`` / ``video_gen_fail_count`` и
    ``shot2_status``) снимаются — иначе кадр остался бы «пропущенным».
    """
    # Шаг видео тянет за собой ботов — импорт только в момент вызова ручки.
    from app.orchestrator.steps.generate_videos import VIDEO_FAIL_ATTR, VIDEO_SKIP_ATTR

    f = await session.get(Frame, frame_id)
    if f is None or f.project_id != project_id:
        raise HTTPException(status_code=404, detail="frame not found")

    project = await session.get(Project, project_id)
    if project is not None:
        videos_dir = project.data_dir / "videos"
        if videos_dir.is_dir():
            keep = videos_dir / "__regenerate_none__.mp4"
            for shot in (1, 2):
                archive_older_frame_clips(videos_dir, f.number, shot=shot, keep=keep)

    await session.execute(
        delete(Artifact).where(
            Artifact.project_id == project_id,
            Artifact.frame_id == frame_id,
            Artifact.kind == ArtifactKind.scene_video,
        )
    )

    attrs = dict(f.attrs or {})
    for key in (
        VIDEO_SKIP_ATTR,
        VIDEO_INFLIGHT_ATTR,
        VIDEO_FAIL_ATTR,
        SHOT2_STATUS_ATTR,
        "video_inflight",  # имя из форка заказчика: могло попасть в старые кадры
    ):
        attrs.pop(key, None)
    f.attrs = attrs
    # У форка тут FrameStatus.ready, которого в нашей модели нет: откатываем
    # кадр ровно на шаг назад — к промту анимации (или к картинке, если промта
    # ещё нет). Раньше video_generated шаг видео считал кадр сделанным.
    if f.status in (FrameStatus.video_generated, FrameStatus.video_approved, FrameStatus.done):
        f.status = (
            FrameStatus.animation_prompt_ready
            if (f.animation_prompt or "").strip()
            else FrameStatus.image_approved
        )
    await commit_with_retry(session)
    return {"ok": True, "frame_id": frame_id, "frame_number": f.number}
