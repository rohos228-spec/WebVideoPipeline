"""Сверка и восстановление артефактов на диске и в БД (Disk-to-DB Reconciler).

Решает проблему рассинхрона данных:
- На диске накоплены тысячи сгенерированных файлов (14,000+ картинок/видео),
  но в БД они либо отсутствуют (сбой транзакции/откат), либо сохранены
  без привязки к арендатору (`tenant_id = None`), из-за чего статистика
  по пользователям («по людям») формируется некорректно.
- Данный сервис:
  1. Сканирует `data/videos/`, `data/batches/`, директории проектов.
  2. Восстанавливает отсутствующие `Artifact` с точной привязкой к `tenant_id`,
     `project_id`, `frame_id`, `kind` и метаданным (номер шота, таймстемпы).
  3. Проставляет недостающий `tenant_id` на уже существующих артефактах и вызовах `MediaCall` / `LlmCall`.
  4. Обновляет статусы кадров (`FrameStatus.image_generated` / `video_generated`).
  5. Генерирует быстрые `.webp` миниатюры в `.thumbs/` для ускорения отображения в UI.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from loguru import logger
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Artifact,
    ArtifactKind,
    Frame,
    FrameStatus,
    LlmCall,
    MediaCall,
    Project,
)
from app.services.plan_shot2 import _IMG_EXTENSIONS
from app.services.thumbnails import thumbnail_path

_FRAME_IMG_RE = re.compile(r"^frame_(\d{3})(?:_s(\d+))?_.*\.(png|jpg|jpeg|webp)$", re.I)
_FRAME_IMG_SIMPLE_RE = re.compile(r"^frame_(\d{3})(?:_s(\d+))?.*\.(png|jpg|jpeg|webp)$", re.I)
_CLIP_VIDEO_RE = re.compile(r"^clip_(\d{3})(?:_s(\d+))?_.*\.mp4$", re.I)
_CLIP_VIDEO_SIMPLE_RE = re.compile(r"^clip_(\d{3})(?:_s(\d+))?.*\.mp4$", re.I)
_AUDIO_EXTENSIONS = frozenset({".mp3", ".wav", ".aac", ".m4a"})


@dataclass
class ReconcileStats:
    """Результат сверки и восстановления артефактов."""

    projects_scanned: int = 0
    artifacts_found_on_disk: int = 0
    artifacts_already_in_db: int = 0
    artifacts_created: int = 0
    artifacts_tenants_repaired: int = 0
    frames_status_updated: int = 0
    thumbnails_generated: int = 0
    media_calls_repaired: int = 0
    llm_calls_repaired: int = 0
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "projects_scanned": self.projects_scanned,
            "artifacts_found_on_disk": self.artifacts_found_on_disk,
            "artifacts_already_in_db": self.artifacts_already_in_db,
            "artifacts_created": self.artifacts_created,
            "artifacts_tenants_repaired": self.artifacts_tenants_repaired,
            "frames_status_updated": self.frames_status_updated,
            "thumbnails_generated": self.thumbnails_generated,
            "media_calls_repaired": self.media_calls_repaired,
            "llm_calls_repaired": self.llm_calls_repaired,
            "errors_count": len(self.errors),
            "errors": self.errors[:20],
        }


def parse_frame_file(filename: str) -> tuple[int, int] | None:
    """Парсинг номера кадра и шота из имени файла (например, frame_003_s2_xyz.png -> (3, 2))."""
    m = _FRAME_IMG_RE.match(filename) or _FRAME_IMG_SIMPLE_RE.match(filename)
    if not m:
        m_vid = _CLIP_VIDEO_RE.match(filename) or _CLIP_VIDEO_SIMPLE_RE.match(filename)
        if not m_vid:
            return None
        num = int(m_vid.group(1))
        shot = int(m_vid.group(2)) if m_vid.group(2) else (2 if "_s2_" in filename.lower() else 1)
        return (num, shot)

    num = int(m.group(1))
    shot = int(m.group(2)) if m.group(2) else (2 if "_s2_" in filename.lower() else 1)
    return (num, shot)


async def reconcile_project(
    session: AsyncSession,
    project: Project,
    *,
    generate_thumbs: bool = True,
    dry_run: bool = False,
    stats: ReconcileStats | None = None,
) -> ReconcileStats:
    """Выполняет полную сверку диска и БД для одного проекта."""
    if stats is None:
        stats = ReconcileStats()

    stats.projects_scanned += 1
    p_data_dir: Path | None = None
    if project.meta and isinstance(project.meta, dict) and project.meta.get("data_dir"):
        custom = Path(project.meta["data_dir"])
        if custom.is_dir():
            p_data_dir = custom
    if p_data_dir is None:
        p_data_dir = project.data_dir
    if not p_data_dir or not p_data_dir.is_dir():
        return stats

    tenant_id = project.tenant_id

    # 1. Загружаем существующие кадры проекта
    frames_q = await session.execute(
        select(Frame).where(Frame.project_id == project.id).order_by(Frame.number)
    )
    frames_by_number: dict[int, Frame] = {f.number: f for f in frames_q.scalars().all()}

    # 2. Загружаем существующие артефакты проекта
    arts_q = await session.execute(
        select(Artifact).where(Artifact.project_id == project.id)
    )
    existing_artifacts = list(arts_q.scalars().all())

    # Индексируем существующие артефакты по нормализованному пути
    arts_by_path: dict[str, Artifact] = {}
    for a in existing_artifacts:
        if a.path:
            norm = str(Path(a.path).resolve()).lower()
            arts_by_path[norm] = a
            # Если у артефакта нет tenant_id, а у проекта есть — чиним
            if tenant_id and not a.tenant_id:
                if not dry_run:
                    a.tenant_id = tenant_id
                stats.artifacts_tenants_repaired += 1

    # 3. Сканирование папки scenes/
    scenes_dir = p_data_dir / "scenes"
    if scenes_dir.is_dir():
        for file_path in scenes_dir.iterdir():
            if not file_path.is_file():
                continue
            suffix = file_path.suffix.lower()
            if suffix not in _IMG_EXTENSIONS:
                continue

            stats.artifacts_found_on_disk += 1
            norm_p = str(file_path.resolve()).lower()
            existing = arts_by_path.get(norm_p)

            parsed = parse_frame_file(file_path.name)
            shot = parsed[1] if parsed else (2 if "_s2_" in file_path.name else 1)
            frame_num = parsed[0] if parsed else None
            frame = frames_by_number.get(frame_num) if frame_num is not None else None

            if existing is not None:
                stats.artifacts_already_in_db += 1
                if tenant_id and not existing.tenant_id:
                    if not dry_run:
                        existing.tenant_id = tenant_id
                    stats.artifacts_tenants_repaired += 1
            else:
                # Создаем отсутствующий Artifact
                stats.artifacts_created += 1
                if not dry_run:
                    try:
                        mtime = file_path.stat().st_mtime
                        created_dt = datetime.fromtimestamp(mtime, tz=UTC).replace(tzinfo=None)
                    except OSError:
                        created_dt = datetime.now(UTC).replace(tzinfo=None)

                    new_art = Artifact(
                        tenant_id=tenant_id,
                        project_id=project.id,
                        frame_id=frame.id if frame else None,
                        kind=ArtifactKind.scene_image,
                        uuid=uuid.uuid4().hex,
                        path=str(file_path.resolve()),
                        meta={"shot": shot, "recovered": True, "source": "artifact_reconciler"},
                        created_at=created_dt,
                    )
                    session.add(new_art)
                    arts_by_path[norm_p] = new_art

                # Обновляем статус кадра если нужно
                if frame and frame.status in (
                    FrameStatus.planned,
                    FrameStatus.image_prompt_ready,
                    FrameStatus.failed,
                ):
                    if not dry_run:
                        frame.status = FrameStatus.image_generated
                    stats.frames_status_updated += 1

            # Миниатюра
            if generate_thumbs:
                try:
                    thumb = thumbnail_path(file_path)
                    if thumb and thumb.is_file():
                        stats.thumbnails_generated += 1
                except Exception as e:
                    stats.errors.append(f"Thumb failed for {file_path.name}: {e}")

    # 4. Сканирование папки videos/
    videos_dir = p_data_dir / "videos"
    if videos_dir.is_dir():
        for file_path in videos_dir.iterdir():
            if not file_path.is_file() or file_path.suffix.lower() != ".mp4":
                continue

            stats.artifacts_found_on_disk += 1
            norm_p = str(file_path.resolve()).lower()
            existing = arts_by_path.get(norm_p)

            parsed = parse_frame_file(file_path.name)
            shot = parsed[1] if parsed else (2 if "_s2_" in file_path.name else 1)
            frame_num = parsed[0] if parsed else None
            frame = frames_by_number.get(frame_num) if frame_num is not None else None

            if existing is not None:
                stats.artifacts_already_in_db += 1
                if tenant_id and not existing.tenant_id:
                    if not dry_run:
                        existing.tenant_id = tenant_id
                    stats.artifacts_tenants_repaired += 1
            else:
                stats.artifacts_created += 1
                if not dry_run:
                    try:
                        mtime = file_path.stat().st_mtime
                        created_dt = datetime.fromtimestamp(mtime, tz=UTC).replace(tzinfo=None)
                    except OSError:
                        created_dt = datetime.now(UTC).replace(tzinfo=None)

                    new_art = Artifact(
                        tenant_id=tenant_id,
                        project_id=project.id,
                        frame_id=frame.id if frame else None,
                        kind=ArtifactKind.scene_video,
                        uuid=uuid.uuid4().hex,
                        path=str(file_path.resolve()),
                        meta={"shot": shot, "recovered": True, "source": "artifact_reconciler"},
                        created_at=created_dt,
                    )
                    session.add(new_art)
                    arts_by_path[norm_p] = new_art

                if frame and frame.status in (
                    FrameStatus.planned,
                    FrameStatus.image_generated,
                    FrameStatus.animation_prompt_ready,
                ):
                    if not dry_run:
                        frame.status = FrameStatus.video_generated
                    stats.frames_status_updated += 1

    # 5. Сканирование папки characters/ (референсы героев)
    chars_dir = p_data_dir / "characters"
    if chars_dir.is_dir():
        for file_path in chars_dir.iterdir():
            if not file_path.is_file() or file_path.suffix.lower() not in _IMG_EXTENSIONS:
                continue

            stats.artifacts_found_on_disk += 1
            norm_p = str(file_path.resolve()).lower()
            existing = arts_by_path.get(norm_p)

            if existing is not None:
                stats.artifacts_already_in_db += 1
                if tenant_id and not existing.tenant_id:
                    if not dry_run:
                        existing.tenant_id = tenant_id
                    stats.artifacts_tenants_repaired += 1
            else:
                stats.artifacts_created += 1
                if not dry_run:
                    new_art = Artifact(
                        tenant_id=tenant_id,
                        project_id=project.id,
                        frame_id=None,
                        kind=ArtifactKind.hero_reference,
                        uuid=uuid.uuid4().hex,
                        path=str(file_path.resolve()),
                        meta={"recovered": True, "source": "artifact_reconciler"},
                    )
                    session.add(new_art)
                    arts_by_path[norm_p] = new_art

            if generate_thumbs:
                try:
                    thumb = thumbnail_path(file_path)
                    if thumb and thumb.is_file():
                        stats.thumbnails_generated += 1
                except Exception:
                    pass

    # 6. Сканирование папки audio/
    audio_dir = p_data_dir / "audio"
    if audio_dir.is_dir():
        for file_path in audio_dir.iterdir():
            if not file_path.is_file() or file_path.suffix.lower() not in _AUDIO_EXTENSIONS:
                continue

            stats.artifacts_found_on_disk += 1
            norm_p = str(file_path.resolve()).lower()
            existing = arts_by_path.get(norm_p)

            if existing is not None:
                stats.artifacts_already_in_db += 1
                if tenant_id and not existing.tenant_id:
                    if not dry_run:
                        existing.tenant_id = tenant_id
                    stats.artifacts_tenants_repaired += 1
            else:
                stats.artifacts_created += 1
                if not dry_run:
                    new_art = Artifact(
                        tenant_id=tenant_id,
                        project_id=project.id,
                        frame_id=None,
                        kind=ArtifactKind.audio,
                        uuid=uuid.uuid4().hex,
                        path=str(file_path.resolve()),
                        meta={"recovered": True, "source": "artifact_reconciler"},
                    )
                    session.add(new_art)
                    arts_by_path[norm_p] = new_art

    # 7. Починка tenant_id в MediaCall и LlmCall для проекта
    if tenant_id and not dry_run:
        # MediaCall
        mc_res = await session.execute(
            update(MediaCall)
            .where(MediaCall.project_id == project.id, MediaCall.tenant_id.is_(None))
            .values(tenant_id=tenant_id)
        )
        mc_count = getattr(mc_res, "rowcount", 0) or 0
        stats.media_calls_repaired += int(mc_count)

        # LlmCall
        llm_res = await session.execute(
            update(LlmCall)
            .where(LlmCall.project_id == project.id, LlmCall.tenant_id.is_(None))
            .values(tenant_id=tenant_id)
        )
        llm_count = getattr(llm_res, "rowcount", 0) or 0
        stats.llm_calls_repaired += int(llm_count)

    if not dry_run:
        await session.flush()

    return stats


async def reconcile_all(
    session: AsyncSession,
    *,
    project_id: int | None = None,
    generate_thumbs: bool = True,
    dry_run: bool = False,
) -> ReconcileStats:
    """Выполняет сверку и восстановление для одного проекта или всех проектов в системе."""
    stats = ReconcileStats()

    if project_id is not None:
        p = await session.get(Project, project_id)
        if not p:
            stats.errors.append(f"Проект #{project_id} не найден в БД")
            return stats
        projects = [p]
    else:
        q = await session.execute(select(Project).order_by(Project.id))
        projects = list(q.scalars().all())

    logger.info(
        "artifact_reconciler: запуск сверки (проектов: {}, dry_run: {})",
        len(projects),
        dry_run,
    )

    for p in projects:
        try:
            await reconcile_project(
                session,
                p,
                generate_thumbs=generate_thumbs,
                dry_run=dry_run,
                stats=stats,
            )
        except Exception as e:
            msg = f"Ошибка сверки проекта #{p.id} ({p.slug}): {e}"
            logger.exception(msg)
            stats.errors.append(msg)

    if not dry_run:
        await session.commit()

    logger.info(
        "artifact_reconciler: сверка завершена. Диск: {}, создано Artifact: {}, "
        "починено tenant_id: {}, миниатюр: {}, статусов кадров: {}",
        stats.artifacts_found_on_disk,
        stats.artifacts_created,
        stats.artifacts_tenants_repaired,
        stats.thumbnails_generated,
        stats.frames_status_updated,
    )

    return stats
