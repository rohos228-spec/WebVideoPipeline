"""REST: /api/artifacts — список + бинарная отдача.

Также /api/files (вне /artifacts) — служит для отдачи media по абсолютному
пути из HITL.payload (photo_path/video_path), полезно когда в БД ещё нет
Artifact.

**Двух проверок мало, нужны обе.** Ограничение «только под `data_dir`»
защищает от выхода за пределы каталога (path traversal) и ничего не говорит
о том, ЧЕЙ это файл. В SaaS `data_dir` общий на всех арендаторов: клиент с
законным токеном, узнав чужой слаг, забирал бы чужой ролик целиком — не
обходя row-level security, а идя мимо неё, потому что файлы политика не
закрывает. Поэтому рядом стоит `file_scope.assert_readable`, который
раскладывает путь до слага проекта и спрашивает базу, видит ли её этот
арендатор.
"""

from __future__ import annotations

import mimetypes
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Artifact, ArtifactKind, Project
from app.services.file_scope import ForeignFile, assert_readable
from app.settings import settings
from app.web.deps import get_session
from app.web.schemas import ArtifactDTO

router = APIRouter(prefix="/artifacts", tags=["artifacts"])
files_router = APIRouter(tags=["files"])


@files_router.get("/files")
async def serve_data_file(
    path: str = Query(..., description="Абсолютный путь под data_dir"),
    download: int = Query(0, description="1 = Content-Disposition: attachment"),
    thumb: int = Query(0, description="1 = уменьшенная WebP-копия для сетки истории"),
    session: AsyncSession = Depends(get_session),
) -> FileResponse:
    """Отдаёт файл из data_dir (или его подкаталогов). Безопасный whitelist
    предотвращает path traversal — допускаем только пути, чей resolve
    начинается с data_dir.resolve().

    Имя на диске (.bin) не доверяем: magic → MIME и filename при download.

    ``thumb=1`` — только для картинок: отдаёт кэшированный WebP 320px
    (ключ — путь + mtime, stale невозможен). Проверки доступа те же, что и
    у оригинала; не-картинки и сбои PIL молча отдают оригинал.
    """
    from app.services.gpt_api import suggested_name_and_mime

    candidate = Path(path).resolve()
    base = Path(settings.data_dir).resolve()
    try:
        candidate.relative_to(base)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="path outside data_dir") from exc
    if not candidate.is_file():
        raise HTTPException(status_code=404, detail="file not found")
    try:
        await assert_readable(session, candidate)
    except ForeignFile as exc:
        # 404, а не 403: существование чужого файла — тоже сведения о чужом
        # проекте. Отвечать «есть, но не дам» значит подтверждать слаг.
        raise HTTPException(status_code=404, detail="file not found") from exc
    if thumb and not download:
        from app.services.thumbnails import thumbnail_path

        small = thumbnail_path(candidate)
        if small is not None:
            return FileResponse(
                small,
                media_type="image/webp",
                headers={"Cache-Control": "private, max-age=31536000, immutable"},
            )
    download_name, mime = suggested_name_and_mime(candidate)
    kwargs: dict = {
        "media_type": mime or "application/octet-stream",
        "headers": {
            # v=mtime в URL — ключ кэша. immutable ломал превью монтажа:
            # после regen браузер держал старый PNG.
            "Cache-Control": "private, max-age=60, must-revalidate",
        },
    }
    if download:
        kwargs["filename"] = download_name
        # download — без долгого кэша в браузере как attachment
        kwargs["headers"] = {"Cache-Control": "private, no-store"}
    return FileResponse(candidate, **kwargs)


@router.get("", response_model=list[ArtifactDTO])
async def list_artifacts(
    project_id: int | None = None,
    frame_id: int | None = None,
    kind: str | None = None,
    session: AsyncSession = Depends(get_session),
) -> list[Artifact]:
    q = select(Artifact)
    if project_id is not None:
        q = q.where(Artifact.project_id == project_id)
    if frame_id is not None:
        q = q.where(Artifact.frame_id == frame_id)
    if kind is not None:
        q = q.where(Artifact.kind == kind)
    rows = (await session.execute(q.order_by(Artifact.id.desc()).limit(500))).scalars().all()
    return list(rows)


@router.get("/project-cover/{project_id}")
async def get_project_cover(
    project_id: int,
    session: AsyncSession = Depends(get_session),
) -> FileResponse:
    """Отдаёт изображение-обложку проекта для карточек в сайдбаре / профиле."""
    project = (await session.execute(select(Project).where(Project.id == project_id))).scalar_one_or_none()
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")

    # 1. Проверяем явный custom cover в project.meta
    meta = (project.meta or {}) if isinstance(project.meta, dict) else {}
    custom_cover = meta.get("cover_image") or meta.get("custom_cover")
    if custom_cover and isinstance(custom_cover, str):
        c_path = Path(custom_cover)
        if c_path.is_file():
            try:
                await assert_readable(session, c_path)
                mime, _ = mimetypes.guess_type(str(c_path))
                return FileResponse(
                    c_path,
                    media_type=mime or "image/png",
                    headers={"Cache-Control": "private, max-age=60, must-revalidate"},
                )
            except Exception:
                pass

    # 2. Ищем артефакты готовых кадров сцены (scene_image)
    scene_arts = (
        await session.execute(
            select(Artifact)
            .where(
                Artifact.project_id == project_id,
                Artifact.kind == ArtifactKind.scene_image,
            )
            .order_by(Artifact.id.asc())
            .limit(10)
        )
    ).scalars().all()
    for art in scene_arts:
        if art.path:
            p = Path(art.path)
            if p.is_file() and p.stat().st_size > 0:
                try:
                    await assert_readable(session, p)
                except Exception:
                    continue
                mime, _ = mimetypes.guess_type(str(p))
                return FileResponse(
                    p,
                    media_type=mime or "image/png",
                    headers={"Cache-Control": "private, max-age=60, must-revalidate"},
                )

    # 3. Ищем другие графические артефакты (hero_reference, item_reference, image)
    other_arts = (
        await session.execute(
            select(Artifact)
            .where(
                Artifact.project_id == project_id,
                Artifact.kind.in_([
                    ArtifactKind.hero_reference,
                    ArtifactKind.item_reference,
                    "image",
                    "hero",
                    "item",
                ]),
            )
            .order_by(Artifact.id.asc())
            .limit(10)
        )
    ).scalars().all()
    for art in other_arts:
        if art.path:
            p = Path(art.path)
            if p.is_file() and p.stat().st_size > 0:
                try:
                    await assert_readable(session, p)
                except Exception:
                    continue
                mime, _ = mimetypes.guess_type(str(p))
                return FileResponse(
                    p,
                    media_type=mime or "image/png",
                    headers={"Cache-Control": "private, max-age=60, must-revalidate"},
                )

    # 4. Фоллбэк: файлы на диске проекта (data_dir / images или frames)
    if project.data_dir:
        p_dir = Path(project.data_dir)
        for folder in ("images", "frames"):
            f_dir = p_dir / folder
            if f_dir.is_dir():
                for ext in ("*.png", "*.webp", "*.jpg", "*.jpeg"):
                    for candidate in sorted(f_dir.glob(ext)):
                        if candidate.is_file() and candidate.stat().st_size > 0:
                            try:
                                await assert_readable(session, candidate)
                            except Exception:
                                continue
                            mime, _ = mimetypes.guess_type(str(candidate))
                            return FileResponse(
                                candidate,
                                media_type=mime or "image/png",
                                headers={"Cache-Control": "private, max-age=60, must-revalidate"},
                            )

    raise HTTPException(status_code=404, detail="project cover not found")


@router.get("/{artifact_uuid}", response_model=ArtifactDTO)
async def get_artifact(artifact_uuid: str, session: AsyncSession = Depends(get_session)) -> Artifact:
    a = (await session.execute(select(Artifact).where(Artifact.uuid == artifact_uuid))).scalar_one_or_none()
    if a is None:
        raise HTTPException(status_code=404, detail="artifact not found")
    return a


@router.get("/{artifact_uuid}/file")
async def download_artifact(artifact_uuid: str, session: AsyncSession = Depends(get_session)):
    """Отдать артефакт. Опубликованный — подписанной ссылкой, мимо приложения.

    Ролик это десятки мегабайт, и каждый просмотр через FastAPI занимает
    воркер на всё время скачивания. Пока объектного хранилища нет (режим
    владельца), поведение прежнее: файл с диска.

    Чужой артефакт сюда не доходит — `artifacts` под политикой RLS, и запрос
    по uuid чужой строки просто не найдёт.
    """
    a = (await session.execute(select(Artifact).where(Artifact.uuid == artifact_uuid))).scalar_one_or_none()
    if a is None:
        raise HTTPException(status_code=404, detail="artifact not found")

    if getattr(a, "storage_key", ""):
        from fastapi.responses import RedirectResponse

        from app.services.artifact_storage import artifact_url

        # 307, а не 302: метод обязан сохраниться, а ссылка одноразовая по
        # сроку — кэшировать перенаправление на неё нельзя.
        return RedirectResponse(await artifact_url(a), status_code=307)

    path = Path(a.path)
    if not path.is_file():
        raise HTTPException(status_code=410, detail="file gone from disk")
    mime, _ = mimetypes.guess_type(str(path))
    return FileResponse(path, media_type=mime or "application/octet-stream")
