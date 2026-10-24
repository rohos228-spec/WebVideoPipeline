"""Профиль пользователя, статистика и ключи: `/api/profile/*`."""

from __future__ import annotations

import json
import mimetypes
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Frame, MediaCall, Project, StudioUser
from app.settings import settings
from app.web.deps import get_session

router = APIRouter(prefix="/profile", tags=["profile"])

_KEY_ATTRS: dict[str, str] = {
    "kie": "kie_api_key",
    "elevenlabs": "elevenlabs_api_key",
    "outsee": "outsee_api_key",
    "gpt": "gpt_api_key",
    "wavespeed": "wavespeed_api_key",
    "minimax": "minimax_api_key",
}

_AVATAR_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}


def _mask_key(k: str) -> str:
    s = (k or "").strip()
    if not s:
        return ""
    if len(s) <= 8:
        return "****"
    return f"{s[:3]}...{s[-4:]}"


def _profiles_dir() -> Path:
    path = settings.data_dir / "profiles"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _avatars_dir() -> Path:
    path = settings.data_dir / "avatars"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _keys_path() -> Path:
    return settings.data_dir / "studio_api_keys.json"


def _tenant_key(identity) -> str:
    if identity is None:
        return "local-owner"
    return str(identity.tenant_id)


def _meta_path(tenant_key: str) -> Path:
    return _profiles_dir() / f"{tenant_key}.json"


def _load_meta(tenant_key: str) -> dict:
    path = _meta_path(tenant_key)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_meta(tenant_key: str, meta: dict) -> None:
    path = _meta_path(tenant_key)
    path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def _apply_persisted_keys() -> None:
    path = _keys_path()
    if not path.is_file():
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(data, dict):
        return
    for key_id, attr in _KEY_ATTRS.items():
        val = data.get(key_id)
        if isinstance(val, str) and val.strip():
            setattr(settings, attr, val.strip())


def _persist_key(key_id: str, value: str) -> None:
    path = _keys_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data: dict = {}
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
        except (OSError, json.JSONDecodeError):
            data = {}
    data[key_id] = value
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _find_avatar_file(tenant_key: str) -> Path | None:
    for ext in sorted(_AVATAR_EXTS):
        candidate = _avatars_dir() / f"{tenant_key}{ext}"
        if candidate.is_file():
            return candidate
    return None


def _avatar_url_for(tenant_key: str) -> str | None:
    if _find_avatar_file(tenant_key) is None:
        return None
    return "/api/profile/avatar-file"


class ProfileOut(BaseModel):
    name: str
    handle: str
    bio: str = "Режиссёр AI-видео"
    plan: str = "Pro"
    avatar_url: str | None = None
    created_at: str | None = None
    notifications: dict[str, bool] = Field(default_factory=lambda: {"email": True, "browser": True})


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
    totals_by_kind: dict[str, int] = Field(default_factory=dict)
    per_project: dict[str, dict[str, int]] = Field(default_factory=dict)
    daily: list[DayStat] = Field(default_factory=list)
    status_counts: dict[str, int] = Field(default_factory=dict)


class KeyItem(BaseModel):
    id: str
    label: str
    env: str
    configured: bool
    masked: str


class KeyUpdateIn(BaseModel):
    value: str


def _build_profile(user: StudioUser | None, identity, meta: dict, tenant_key: str) -> ProfileOut:
    if identity is None and user is None:
        notif = meta.get("notifications")
        if not isinstance(notif, dict):
            notif = {"email": True, "browser": True}
        return ProfileOut(
            name=str(meta.get("name") or "Владелец студии"),
            handle=str(meta.get("handle") or "owner"),
            bio=str(meta.get("bio") or "Локальный запуск"),
            plan="Pro",
            avatar_url=_avatar_url_for(tenant_key) or meta.get("avatar_url"),
            created_at=datetime.now(UTC).isoformat(),
            notifications={
                "email": bool(notif.get("email", True)),
                "browser": bool(notif.get("browser", True)),
            },
        )

    email = user.email if user else (getattr(identity, "email", None) or "user@studio.local")
    default_handle = email.split("@")[0]
    handle = str(meta.get("handle") or default_handle)
    name = (
        (user.display_name if user and user.display_name else None)
        or meta.get("name")
        or handle
    )
    plan = "Pro" if (user and user.role == "admin") or (identity and identity.is_admin) else "Free"
    notifications = meta.get("notifications")
    if not isinstance(notifications, dict):
        notifications = {"email": True, "browser": True}

    return ProfileOut(
        name=str(name),
        handle=handle,
        bio=str(meta.get("bio") or "Режиссёр AI-видео"),
        plan=plan,
        avatar_url=_avatar_url_for(tenant_key) or meta.get("avatar_url"),
        created_at=user.created_at.isoformat() if user and user.created_at else datetime.now(UTC).isoformat(),
        notifications={
            "email": bool(notifications.get("email", True)),
            "browser": bool(notifications.get("browser", True)),
        },
    )


@router.get("", response_model=ProfileOut)
async def get_profile(request: Request, session: AsyncSession = Depends(get_session)) -> ProfileOut:
    identity = request.scope.get("studio_identity")
    tenant_key = _tenant_key(identity)
    meta = _load_meta(tenant_key)
    user = None
    if identity is not None:
        user = await session.get(StudioUser, identity.tenant_id)
    return _build_profile(user, identity, meta, tenant_key)


@router.put("", response_model=ProfileOut)
async def update_profile(
    body: ProfileUpdateIn,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> ProfileOut:
    identity = request.scope.get("studio_identity")
    tenant_key = _tenant_key(identity)
    meta = _load_meta(tenant_key)

    if identity is not None:
        user = await session.get(StudioUser, identity.tenant_id)
        if user is not None and body.name is not None and body.name.strip():
            user.display_name = body.name.strip()
            await session.commit()

    if body.name is not None and body.name.strip():
        meta["name"] = body.name.strip()
    if body.handle is not None and body.handle.strip():
        meta["handle"] = body.handle.strip().lstrip("@")[:64]
    if body.bio is not None:
        meta["bio"] = body.bio.strip()[:500]
    if body.notifications is not None:
        meta["notifications"] = {
            "email": bool(body.notifications.get("email", True)),
            "browser": bool(body.notifications.get("browser", True)),
        }
    _save_meta(tenant_key, meta)
    return await get_profile(request, session)


@router.post("/avatar", response_model=ProfileOut)
async def upload_avatar(
    request: Request,
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
) -> ProfileOut:
    identity = request.scope.get("studio_identity")
    tenant_key = _tenant_key(identity)
    raw_name = file.filename or "avatar.png"
    ext = Path(raw_name).suffix.lower()
    if ext not in _AVATAR_EXTS:
        # sniff from content-type
        ctype = (file.content_type or "").lower()
        ext = {
            "image/jpeg": ".jpg",
            "image/png": ".png",
            "image/webp": ".webp",
            "image/gif": ".gif",
        }.get(ctype, "")
    if ext not in _AVATAR_EXTS:
        raise HTTPException(status_code=400, detail="Нужен файл изображения (jpg/png/webp/gif)")

    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Пустой файл")
    if len(data) > 5 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Аватар больше 5 МБ")

    # remove old variants
    for old in _avatars_dir().glob(f"{tenant_key}.*"):
        try:
            old.unlink()
        except OSError:
            pass

    dest = _avatars_dir() / f"{tenant_key}{ext}"
    dest.write_bytes(data)

    meta = _load_meta(tenant_key)
    meta["avatar_url"] = "/api/profile/avatar-file"
    _save_meta(tenant_key, meta)
    return await get_profile(request, session)


@router.delete("/avatar", response_model=ProfileOut)
async def delete_avatar(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> ProfileOut:
    identity = request.scope.get("studio_identity")
    tenant_key = _tenant_key(identity)
    for old in _avatars_dir().glob(f"{tenant_key}.*"):
        try:
            old.unlink()
        except OSError:
            pass
    meta = _load_meta(tenant_key)
    meta.pop("avatar_url", None)
    _save_meta(tenant_key, meta)
    return await get_profile(request, session)


@router.get("/avatar-file")
async def get_avatar_file(request: Request):
    identity = request.scope.get("studio_identity")
    tenant_key = _tenant_key(identity)
    path = _find_avatar_file(tenant_key)
    if path is None:
        raise HTTPException(status_code=404, detail="Аватар не задан")
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return FileResponse(path, media_type=media_type)


@router.get("/keys", response_model=list[KeyItem])
async def list_keys() -> list[KeyItem]:
    """Список подключений и статус API-ключей из настроек студии."""
    _apply_persisted_keys()
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

    if key_id not in _KEY_ATTRS:
        raise HTTPException(status_code=404, detail=f"Неизвестный ключ: {key_id}")

    val = body.value.strip()
    attr = _KEY_ATTRS[key_id]
    setattr(settings, attr, val)
    _persist_key(key_id, val)

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
    daily_map: dict[str, DayStat] = {}
    for i in range(29, -1, -1):
        d_str = (today - timedelta(days=i)).isoformat()
        daily_map[d_str] = DayStat(date=d_str)

    since = datetime.now(UTC) - timedelta(days=30)
    q_media = select(MediaCall).where(MediaCall.created_at >= since, MediaCall.result == "ok")
    if tenant_id and not (identity and identity.is_admin):
        q_media = q_media.where(MediaCall.tenant_id == tenant_id)
    media_rows = (await session.execute(q_media)).scalars().all()
    for row in media_rows:
        created = row.created_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=UTC)
        d_str = created.astimezone(UTC).date().isoformat()
        if d_str not in daily_map:
            continue
        bucket = daily_map[d_str]
        kind = (row.kind or "").lower()
        if kind == "image":
            bucket.images += 1
        elif kind == "video":
            bucket.videos += 1
        elif kind in ("tts", "music", "audio"):
            bucket.audio += 1

    for p in projects:
        st = str(p.status.value if hasattr(p.status, "value") else p.status)
        if st not in ("done", "published", "complete", "music_ready", "assembled"):
            continue
        updated = getattr(p, "updated_at", None) or getattr(p, "created_at", None)
        if updated is None:
            continue
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=UTC)
        d_str = updated.astimezone(UTC).date().isoformat()
        if d_str in daily_map:
            daily_map[d_str].finals += 1

    daily = [daily_map[(today - timedelta(days=i)).isoformat()] for i in range(29, -1, -1)]

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
