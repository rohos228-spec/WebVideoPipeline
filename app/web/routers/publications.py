"""Публикации проекта в модели заказчика: только чтение.

Шаг `publish` (app/orchestrator/steps/publish.py) выкладывает финальный ролик сразу
на все площадки, где его ещё нет, через профиль MoreLogin, и пишет итог в
`project.meta["published_platforms"]` (площадка → ссылка). Отдельной панели
разрешений по площадкам у заказчика нет: публикацию запускает человек — из чата
карточкой `publish.send` с подтверждением. Эта ручка показывает состояние и даёт
действию `publish.send` факты (sha256 файла и подписи) для текста карточки.
"""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Artifact, ArtifactKind, Project
from app.web.deps import get_session

router = APIRouter(prefix="/projects", tags=["publications"])

ProjectSession = Annotated[AsyncSession, Depends(get_session)]

_SHA_CACHE: dict[tuple[str, int, int], str] = {}


def _file_sha256(path: str) -> str | None:
    p = Path(path)
    try:
        st = p.stat()
    except OSError:
        return None
    key = (str(p), st.st_size, int(st.st_mtime))
    got = _SHA_CACHE.get(key)
    if got:
        return got
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    if len(_SHA_CACHE) > 128:
        _SHA_CACHE.clear()
    _SHA_CACHE[key] = h.hexdigest()
    return _SHA_CACHE[key]


def _accounts() -> tuple[dict[str, str], str]:
    """Аккаунт по площадке (маска профиля) и общий текст сбоя конфига."""
    from app.services import publish_accounts as pa
    from app.settings import settings

    try:
        cfg = pa.load_accounts()
    except pa.AccountsConfigError as exc:
        return {}, f"Конфиг аккаунтов публикации содержит ошибку: {exc}"
    if cfg.configured:
        out: dict[str, str] = {}
        for p in pa.KNOWN_PLATFORMS:
            acct = cfg.for_platform(p)
            if not acct.enabled:
                out[p] = "площадка выключена в конфиге"
            elif not acct.profile_id:
                out[p] = "профиль не задан"
            else:
                out[p] = acct.label()
        return out, ""
    pid = str(settings.morelogin_profile_id or "").strip()
    if not pid:
        return {}, "Профиль MoreLogin не задан (MORELOGIN_PROFILE_ID): публикация не начнётся."
    return {p: f"профиль MoreLogin {pa.mask_profile(pid)}" for p in pa.KNOWN_PLATFORMS}, ""


@router.get("/{project_id}/publications")
async def get_publications(project_id: int, session: ProjectSession) -> dict[str, Any]:
    from app.services.action_layer.publish_action import PLATFORM_TITLES, caption_of, registry_platforms
    from app.settings import settings

    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    final = (
        await session.execute(
            select(Artifact)
            .where(Artifact.project_id == project.id, Artifact.kind == ArtifactKind.final_video)
            .order_by(Artifact.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    sha = await asyncio.to_thread(_file_sha256, final.path) if final is not None else None
    meta = project.meta if isinstance(project.meta, dict) else {}
    done = raw_done if isinstance(raw_done := meta.get("published_platforms"), dict) else {}
    accounts, accounts_error = _accounts()
    caption = caption_of({"topic": project.topic, "slug": project.slug})
    status = getattr(project.status, "value", str(project.status))
    platforms = []
    for p in registry_platforms():
        published = p in done
        platforms.append(
            {
                "platform": p,
                "title": PLATFORM_TITLES.get(p, p),
                "published": published,
                "post_url": (str(done.get(p) or "") or None) if published else None,
                "account": accounts.get(p, ""),
                # Разрешений по площадкам у заказчика нет: подтверждает человек карточку в чате.
                "approved": False,
            }
        )
    pending = [x["title"] for x in platforms if not x["published"]]
    return {
        "project_id": project.id,
        "project_status": status,
        "publish_enabled": bool(settings.social_publish_enabled),
        "sha256": sha,
        "caption": caption,
        "caption_sha256": hashlib.sha256(caption.encode("utf-8")).hexdigest(),
        "video": {"artifact_uuid": final.uuid, "url": f"/api/artifacts/{final.uuid}/file"} if final else None,
        "accounts_error": accounts_error,
        "platforms": platforms,
        "pending": pending,
    }
