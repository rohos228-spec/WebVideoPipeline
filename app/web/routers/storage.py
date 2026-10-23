"""Управление дисковым пространством и очистка кэша: `/api/storage/*`.

Доступно только администраторам (в режиме владельца доступ открыт).
Предоставляет:
- GET /api/storage/usage — статистика занятого места по типам файлов, проектам и диску
- GET /api/storage/cache — оценка размера временных файлов и кэша
- POST /api/storage/cache/clear — безопасная очистка кэша и временных файлов
- POST /api/storage/export/{project_id} — сборка zip-архива проекта
- GET /api/storage/export/download/{filename} — скачивание архива
"""

from __future__ import annotations

import os
import shutil
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Project
from app.services.studio_auth import current_identity
from app.settings import settings
from app.web.deps import get_session

router = APIRouter(prefix="/storage", tags=["storage"])

VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".webm"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
AUDIO_EXTS = {".mp3", ".wav", ".aac", ".flac", ".ogg", ".m4a"}
PROMPT_EXTS = {".md", ".txt", ".json", ".xlsx", ".csv"}


def _admin_only() -> None:
    """Ограничение доступа: только для администраторов (участники получают 404)."""
    identity = current_identity()
    if identity is not None and not identity.is_admin:
        raise HTTPException(status_code=404, detail="Раздел не найден")


def _get_disk_info() -> dict[str, Any]:
    try:
        data_path = Path(settings.data_dir).resolve()
        total, used, free = shutil.disk_usage(data_path)
        drive = data_path.drive or "/"
        return {"total": total, "used": used, "free": free, "drive": drive}
    except Exception as e:
        logger.warning("storage: disk_usage error: {}", e)
        return {"total": 0, "used": 0, "free": 0, "drive": ""}


def _classify_ext(suffix: str) -> str:
    s = suffix.lower()
    if s in VIDEO_EXTS:
        return "video"
    if s in IMAGE_EXTS:
        return "image"
    if s in AUDIO_EXTS:
        return "audio"
    if s in PROMPT_EXTS:
        return "prompts"
    return "other"


@dataclass
class _UsageCache:
    data: dict[str, Any]
    timestamp: float


_cached_usage: _UsageCache | None = None
_USAGE_TTL_S = 60.0


async def _scan_usage(session: AsyncSession) -> dict[str, Any]:
    disk_info = _get_disk_info()
    data_dir = Path(settings.data_dir).resolve()

    by_type: dict[str, int] = {
        "video": 0,
        "image": 0,
        "audio": 0,
        "prompts": 0,
        "other": 0,
    }
    total_bytes = 0
    total_files = 0
    project_stats: list[dict[str, Any]] = []

    # 1. Сканируем проекты из базы
    projects = (await session.execute(select(Project))).scalars().all()
    project_dirs_seen: set[Path] = set()

    for p in projects:
        p_bytes = 0
        p_files = 0
        try:
            p_dir = p.data_dir.resolve()
            if p_dir.is_dir():
                project_dirs_seen.add(p_dir)
                for root, _, files in os.walk(p_dir):
                    for fname in files:
                        fpath = Path(root) / fname
                        try:
                            st = fpath.stat()
                            sz = st.st_size
                            p_bytes += sz
                            p_files += 1
                            total_bytes += sz
                            total_files += 1
                            kind = _classify_ext(fpath.suffix)
                            by_type[kind] = by_type.get(kind, 0) + sz
                        except OSError:
                            continue
        except Exception as e:
            logger.debug("storage: scan project #{} error: {}", p.id, e)

        project_stats.append({
            "id": p.id,
            "bytes": p_bytes,
            "files": p_files,
        })

    # 2. Сканируем глобальные директории (library, generations, voices), не входящие в проекты
    other_dirs = ["library", "generations", "voices", "videos"]
    for dname in other_dirs:
        top_dir = data_dir / dname
        if not top_dir.is_dir():
            continue
        for root, _, files in os.walk(top_dir):
            rpath = Path(root).resolve()
            # Пропускаем папки уже подсчитанных проектов
            if any(rpath == pd or pd in rpath.parents for pd in project_dirs_seen):
                continue
            for fname in files:
                fpath = rpath / fname
                try:
                    sz = fpath.stat().st_size
                    total_bytes += sz
                    total_files += 1
                    kind = _classify_ext(fpath.suffix)
                    by_type[kind] = by_type.get(kind, 0) + sz
                except OSError:
                    continue

    return {
        "total_bytes": total_bytes,
        "files": total_files,
        "by_type": by_type,
        "projects": project_stats,
        "disk": disk_info,
        "limit_bytes": disk_info.get("free", 0),
    }


@router.get("/usage", dependencies=[Depends(_admin_only)])
async def get_usage(
    refresh: int = Query(0, description="1 = пересчитать без кэша"),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    global _cached_usage
    now = time.monotonic()
    if refresh or _cached_usage is None or (now - _cached_usage.timestamp > _USAGE_TTL_S):
        data = await _scan_usage(session)
        _cached_usage = _UsageCache(data=data, timestamp=now)
        return data
    return _cached_usage.data


def _get_dir_size_and_count(path: Path) -> tuple[int, int]:
    if not path.is_dir():
        return 0, 0
    total = 0
    count = 0
    for root, _, files in os.walk(path):
        for f in files:
            try:
                total += (Path(root) / f).stat().st_size
                count += 1
            except OSError:
                continue
    return total, count


@router.get("/cache", dependencies=[Depends(_admin_only)])
async def get_cache_info() -> dict[str, Any]:
    data_dir = Path(settings.data_dir).resolve()
    thumbs_dir = data_dir / ".thumbs"
    cache_dir = data_dir / ".cache"

    thumbs_bytes, thumbs_cnt = _get_dir_size_and_count(thumbs_dir)

    # .cache исключая exports
    cache_bytes = 0
    cache_cnt = 0
    if cache_dir.is_dir():
        for root, _, files in os.walk(cache_dir):
            if "exports" in Path(root).parts:
                continue
            for f in files:
                try:
                    cache_bytes += (Path(root) / f).stat().st_size
                    cache_cnt += 1
                except OSError:
                    continue

    # Временные файлы рендера и ffmpeg (temp_*, *.tmp)
    render_tmp_bytes = 0
    render_tmp_cnt = 0
    videos_dir = data_dir / "videos"
    if videos_dir.is_dir():
        for root, _, files in os.walk(videos_dir):
            for f in files:
                if f.startswith("temp_") or f.endswith(".tmp") or f == "concat_list.txt":
                    try:
                        render_tmp_bytes += (Path(root) / f).stat().st_size
                        render_tmp_cnt += 1
                    except OSError:
                        continue

    # Старые логи сервера (старше 15 минут)
    logs_bytes = 0
    logs_cnt = 0
    now_ts = time.time()
    for log_file in data_dir.glob("backend-*.log"):
        try:
            mtime = log_file.stat().st_mtime
            if now_ts - mtime > 900:  # старше 15 минут
                logs_bytes += log_file.stat().st_size
                logs_cnt += 1
        except OSError:
            continue

    total_bytes = thumbs_bytes + cache_bytes + render_tmp_bytes + logs_bytes

    dirs = [
        {"path": f"Миниатюры ({thumbs_cnt} шт., data/.thumbs)", "bytes": thumbs_bytes},
        {"path": f"Кэш системы ({cache_cnt} шт., data/.cache)", "bytes": cache_bytes},
        {"path": f"Временные файлы рендера ({render_tmp_cnt} шт., *.tmp, temp_*)", "bytes": render_tmp_bytes},
        {"path": f"Архивные логи сервера ({logs_cnt} шт., data/backend-*.log)", "bytes": logs_bytes},
    ]

    global _cached_usage
    data_total = _cached_usage.data.get("total_bytes", 0) if _cached_usage else 0
    pct = round((total_bytes / data_total) * 100, 1) if data_total > 0 else 0.0

    return {
        "total_bytes": total_bytes,
        "dirs": dirs,
        "percent_of_data": pct,
    }


@router.post("/cache/clear", dependencies=[Depends(_admin_only)])
async def clear_cache() -> dict[str, Any]:
    global _cached_usage
    data_dir = Path(settings.data_dir).resolve()
    freed_bytes = 0
    deleted_files = 0

    # 1. Очистка .thumbs
    thumbs_dir = data_dir / ".thumbs"
    if thumbs_dir.is_dir():
        for item in thumbs_dir.iterdir():
            try:
                if item.is_file():
                    sz = item.stat().st_size
                    item.unlink()
                    freed_bytes += sz
                    deleted_files += 1
                elif item.is_dir():
                    sz, cnt = _get_dir_size_and_count(item)
                    shutil.rmtree(item, ignore_errors=True)
                    freed_bytes += sz
                    deleted_files += cnt
            except OSError as e:
                logger.debug("storage: clear thumbs item {} error: {}", item, e)

    # 2. Очистка .cache (сохраняем exports)
    cache_dir = data_dir / ".cache"
    if cache_dir.is_dir():
        for item in cache_dir.iterdir():
            if item.name == "exports":
                continue
            try:
                if item.is_file():
                    sz = item.stat().st_size
                    item.unlink()
                    freed_bytes += sz
                    deleted_files += 1
                elif item.is_dir():
                    sz, cnt = _get_dir_size_and_count(item)
                    shutil.rmtree(item, ignore_errors=True)
                    freed_bytes += sz
                    deleted_files += cnt
            except OSError as e:
                logger.debug("storage: clear cache item {} error: {}", item, e)

    # 3. Временные файлы рендера (*.tmp, temp_*)
    videos_dir = data_dir / "videos"
    if videos_dir.is_dir():
        for root, _, files in os.walk(videos_dir):
            for f in files:
                if f.startswith("temp_") or f.endswith(".tmp") or f == "concat_list.txt":
                    fpath = Path(root) / f
                    try:
                        sz = fpath.stat().st_size
                        fpath.unlink()
                        freed_bytes += sz
                        deleted_files += 1
                    except OSError:
                        continue

    # 4. Старые backend-*.log
    now_ts = time.time()
    for log_file in data_dir.glob("backend-*.log"):
        try:
            mtime = log_file.stat().st_mtime
            if now_ts - mtime > 900:
                sz = log_file.stat().st_size
                log_file.unlink()
                freed_bytes += sz
                deleted_files += 1
        except OSError:
            # Заблокированный или активный файл лога пропускаем
            continue

    # Сбрасываем кэш размеров
    _cached_usage = None

    logger.info("storage: cleared cache: freed {} bytes across {} files", freed_bytes, deleted_files)
    return {
        "freed_bytes": freed_bytes,
        "deleted_files": deleted_files,
    }


@router.post("/export/{project_id}", dependencies=[Depends(_admin_only)])
async def export_project(
    project_id: int,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Проект не найден")

    p_dir = project.data_dir.resolve()
    if not p_dir.is_dir():
        raise HTTPException(status_code=404, detail="Папка проекта не найдена на диске")

    exports_dir = Path(settings.data_dir).resolve() / ".cache" / "exports"
    exports_dir.mkdir(parents=True, exist_ok=True)

    slug_safe = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in (project.slug or f"project_{project.id}"))
    zip_name = f"project_{project.id}_{slug_safe}.zip"
    zip_path = exports_dir / zip_name

    # Создаём архив проекта
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, _, files in os.walk(p_dir):
            for file in files:
                fp = Path(root) / file
                arcname = fp.relative_to(p_dir).as_posix()
                try:
                    zf.write(fp, arcname=arcname)
                except OSError as e:
                    logger.debug("storage: export write error {}: {}", fp, e)

    sz = zip_path.stat().st_size
    logger.info("storage: created export archive {} ({} bytes)", zip_name, sz)

    return {
        "path": zip_name,
        "url": f"/api/storage/export/download/{zip_name}",
        "bytes": sz,
    }


@router.get("/export/download/{filename}", dependencies=[Depends(_admin_only)])
async def download_export(filename: str) -> FileResponse:
    # Безопасная проверка пути против path traversal
    safe_name = Path(filename).name
    target = (Path(settings.data_dir).resolve() / ".cache" / "exports" / safe_name).resolve()
    base = (Path(settings.data_dir).resolve() / ".cache" / "exports").resolve()

    try:
        target.relative_to(base)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Недопустимый путь") from exc

    if not target.is_file():
        raise HTTPException(status_code=404, detail="Архив не найден")

    return FileResponse(
        target,
        media_type="application/zip",
        filename=safe_name,
        headers={"Cache-Control": "private, no-store"},
    )
