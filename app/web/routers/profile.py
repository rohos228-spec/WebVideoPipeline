"""Профиль пользователя, статистика и ключи: `/api/profile/*`."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Frame, Project, StudioUser
from app.settings import settings
from app.web.deps import get_session

router = APIRouter(prefix="/profile", tags=["profile"])


def _mask_key(k: str) -> str:
    s = (k or "").strip()
    if not s:
        return ""
    if len(s) <= 8:
        return "****"
    return f"{s[:3]}...{s[-4:]}"


class ProfileOut(BaseModel):
    name: str
    handle: str
    bio: str = "Режиссёр AI-видео"
    plan: str = "Pro"
    avatar_url: str | None = None
    created_at: str | None = None
    notifications: dict[str, bool] = {"email": True, "browser": True}


class ProfileUpdateIn(BaseModel):
    name: str | None = None
    handle: str | None = None
    bio: str | None = None
    notifications: dict[str, bool] | None = None


class DayStat(BaseModel):
    date: str
    images: int = 0
    videos: int = 0
    audio: int = 0
    finals: int = 0


class StatsOut(BaseModel):
    projects: int = 0
    completed_videos: int = 0
    published: int = 0
    generations_total: int = 0
    totals_by_kind: dict[str, int] = {}
    per_project: dict[str, dict[str, int]] = {}
    daily: list[DayStat] = []
    status_counts: dict[str, int] = {}


class KeyItem(BaseModel):
    id: str
    label: str
    env: str
    configured: bool
    masked: str


class KeyUpdateIn(BaseModel):
    value: str


@router.get("", response_model=ProfileOut)
async def get_profile(request: Request, session: AsyncSession = Depends(get_session)) -> ProfileOut:
    identity = request.scope.get("studio_identity")
    if identity is None:
        return ProfileOut(
            name="Владелец студии",
            handle="owner",
            bio="Локальный запуск",
            plan="Pro",
            created_at=datetime.now(UTC).isoformat(),
        )

    user = await session.get(StudioUser, identity.tenant_id)
    email = user.email if user else (identity.email or "user@studio.local")
    handle = email.split("@")[0]
    name = (user.display_name if user and user.display_name else None) or handle

    return ProfileOut(
        name=name,
        handle=handle,
        bio="Режиссёр AI-видео",
        plan="Pro" if (user and user.role == "admin") or identity.is_admin else "Free",
        created_at=user.created_at.isoformat() if user and user.created_at else datetime.now(UTC).isoformat(),
    )


@router.put("", response_model=ProfileOut)
async def update_profile(
    body: ProfileUpdateIn,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> ProfileOut:
    identity = request.scope.get("studio_identity")
    if identity is not None:
        user = await session.get(StudioUser, identity.tenant_id)
        if user is not None:
            if body.name is not None and body.name.strip():
                user.display_name = body.name.strip()
            await session.commit()
    return await get_profile(request, session)


@router.post("/avatar", response_model=ProfileOut)
async def upload_avatar(
    request: Request,
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
) -> ProfileOut:
    return await get_profile(request, session)


@router.delete("/avatar", response_model=ProfileOut)
async def delete_avatar(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> ProfileOut:
    return await get_profile(request, session)


@router.get("/keys", response_model=list[KeyItem])
async def list_keys() -> list[KeyItem]:
    """Список подключений и статус API-ключей из настроек студии."""
    kie_key = settings.kie_api_key
    eleven_key = settings.elevenlabs_api_key
    outsee_key = settings.outsee_api_key
    gpt_key = settings.gpt_api_key or settings.vibecode_api_key
    wavespeed_key = settings.wavespeed_api_key
    minimax_key = settings.minimax_api_key

    return [
        KeyItem(
            id="kie",
            label="Kie AI",
            env="KIE_API_KEY",
            configured=bool(kie_key.strip()),
            masked=_mask_key(kie_key),
        ),
        KeyItem(
            id="elevenlabs",
            label="ElevenLabs",
            env="ELEVENLABS_API_KEY",
            configured=bool(eleven_key.strip()),
            masked=_mask_key(eleven_key),
        ),
        KeyItem(
            id="outsee",
            label="VIA Render",
            env="OUTSEE_API_KEY",
            configured=bool(outsee_key.strip()),
            masked=_mask_key(outsee_key),
        ),
        KeyItem(
            id="gpt",
            label="GPT / Текст",
            env="GPT_API_KEY",
            configured=bool(gpt_key.strip()),
            masked=_mask_key(gpt_key),
        ),
        KeyItem(
            id="wavespeed",
            label="WaveSpeed",
            env="WAVESPEED_API_KEY",
            configured=bool(wavespeed_key.strip()),
            masked=_mask_key(wavespeed_key),
        ),
        KeyItem(
            id="minimax",
            label="MiniMax",
            env="MINIMAX_API_KEY",
            configured=bool(minimax_key.strip()),
            masked=_mask_key(minimax_key),
        ),
    ]


@router.put("/keys/{key_id}", response_model=KeyItem)
async def update_key(key_id: str, body: KeyUpdateIn, request: Request) -> KeyItem:
    identity = request.scope.get("studio_identity")
    if identity is not None and not identity.is_admin:
        raise HTTPException(
            status_code=403,
            detail="Редактирование системных ключей доступно только администратору",
        )

    val = body.value.strip()
    if key_id == "kie":
        settings.kie_api_key = val
    elif key_id == "elevenlabs":
        settings.elevenlabs_api_key = val
    elif key_id == "outsee":
        settings.outsee_api_key = val
    elif key_id == "gpt":
        settings.gpt_api_key = val
    elif key_id == "wavespeed":
        settings.wavespeed_api_key = val
    elif key_id == "minimax":
        settings.minimax_api_key = val

    keys = await list_keys()
    for k in keys:
        if k.id == key_id:
            return k
    return KeyItem(id=key_id, label=key_id, env=key_id.upper(), configured=bool(val), masked=_mask_key(val))


@router.get("/stats", response_model=StatsOut)
async def get_stats(request: Request, session: AsyncSession = Depends(get_session)) -> StatsOut:
    identity = request.scope.get("studio_identity")
    tenant_id = identity.tenant_id if identity else None

    q_proj = select(Project)
    if tenant_id and not (identity and identity.is_admin):
        q_proj = q_proj.where(Project.tenant_id == tenant_id)

    projects = (await session.execute(q_proj)).scalars().all()
    project_ids = [p.id for p in projects]

    status_counts: dict[str, int] = {}
    completed_videos = 0
    published = 0
    for p in projects:
        st = str(p.status.value if hasattr(p.status, "value") else p.status)
        status_counts[st] = status_counts.get(st, 0) + 1
        if st in ("done", "published", "complete", "music_ready", "assembled"):
            completed_videos += 1
        if st == "published":
            published += 1

    totals_by_kind = {
        "scene_image": 0,
        "scene_video": 0,
        "audio": 0,
        "music": 0,
        "hero_reference": 0,
        "item_reference": 0,
    }
    per_project: dict[str, dict[str, int]] = {}
    generations_total = 0

    if project_ids:
        frames = (
            await session.execute(
                select(Frame.project_id, Frame.image_prompt, Frame.animation_prompt).where(
                    Frame.project_id.in_(project_ids)
                )
            )
        ).all()

        for pid, img_pr, anim_pr in frames:
            p_key = str(pid)
            if p_key not in per_project:
                per_project[p_key] = {
                    "images": 0,
                    "videos": 0,
                    "audio": 0,
                    "scene_image": 0,
                    "scene_video": 0,
                    "final_video": 0,
                }
            if img_pr:
                totals_by_kind["scene_image"] += 1
                per_project[p_key]["images"] += 1
                per_project[p_key]["scene_image"] += 1
                generations_total += 1
            if anim_pr:
                totals_by_kind["scene_video"] += 1
                per_project[p_key]["videos"] += 1
                per_project[p_key]["scene_video"] += 1
                generations_total += 1

        for p in projects:
            p_key = str(p.id)
            if p_key in per_project:
                st = str(p.status.value if hasattr(p.status, "value") else p.status)
                if st in ("done", "published", "complete", "music_ready", "assembled"):
                    per_project[p_key]["final_video"] = 1

    today = datetime.now(UTC).date()
    daily: list[DayStat] = []
    for i in range(29, -1, -1):
        d_str = (today - timedelta(days=i)).isoformat()
        daily.append(DayStat(date=d_str, images=0, videos=0, audio=0, finals=0))

    return StatsOut(
        projects=len(projects),
        completed_videos=completed_videos,
        published=published,
        generations_total=generations_total,
        totals_by_kind=totals_by_kind,
        per_project=per_project,
        daily=daily,
        status_counts=status_counts,
    )
