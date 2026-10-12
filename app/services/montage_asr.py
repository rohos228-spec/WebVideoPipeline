"""ASR перед монтажом: words.json для субтитров. Тайминги монтажа — только Excel R15."""

from __future__ import annotations

import asyncio
import re
import uuid
from pathlib import Path

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Artifact, ArtifactKind, Project
from app.services.asr import active_asr_backend, transcribe_words
from app.services.asr_audio_prep import prepare_audio_for_asr
from app.services.nvidia_asr import nvidia_asr_available
from app.services.whisper import WordTS, dump_words_json, whisper_available
from app.settings import settings
from app.storage.plan_sheet_v8 import read_plan_voiceover_cells


def asr_available() -> bool:
    if active_asr_backend() == "nvidia":
        return nvidia_asr_available() or whisper_available()
    return whisper_available()


def get_asr_backend_label() -> str:
    backend = active_asr_backend()
    if backend == "nvidia":
        try:
            import torch

            gpu = torch.cuda.get_device_name(0) if torch.cuda.is_available() else "?"
        except Exception:  # noqa: BLE001
            gpu = "?"
        return f"nvidia:{settings.nvidia_asr_model} (cuda:{gpu})"
    return f"whisper:{settings.whisper_model}"


def build_proportional_words_for_cells(
    cells: list[tuple[int, str]],
    total_duration: float,
) -> list[WordTS]:
    """Строит пропорциональные word-level таймкоды по тексту кадров и длительности звука.

    Используется как fallback, когда на локальной машине нет ASR (NeMo / faster-whisper заблокирован).
    Каждому слову назначается длительность, пропорциональная его длине, с небольшими естественными паузами,
    так чтобы границы кадров строго сходились в total_duration.
    """
    words_by_cell = []
    total_weight = 0.0
    for num, text in cells:
        raw_words = [w for w in re.findall(r"[\wа-яА-ЯёЁ]+", text) if w]
        words_by_cell.append((num, raw_words))
        for w in raw_words:
            total_weight += max(1.5, float(len(w)))
        if raw_words:
            total_weight += 1.0

    if total_weight <= 0:
        return []

    time_per_weight = float(total_duration) / total_weight
    current_time = 0.0
    all_words: list[WordTS] = []
    for _num, raw_words in words_by_cell:
        for w in raw_words:
            dur = max(1.5, float(len(w))) * time_per_weight
            start = round(current_time, 3)
            end = round(current_time + dur, 3)
            all_words.append(WordTS(word=w, start=start, end=end, prob=0.99))
            current_time += dur
        if raw_words:
            current_time += 1.0 * time_per_weight

    if all_words:
        all_words[-1].end = round(total_duration, 3)
    return all_words


async def ensure_montage_words(
    session: AsyncSession,
    project: Project,
    *,
    audio_path: Path,
    audio_dir: Path,
    frame_numbers: list[int],
    existing_words: list[WordTS] | None = None,
) -> list[WordTS]:
    """Word timestamps для субтитров. Не трогает Excel и не считает таймлайн монтажа."""
    words = list(existing_words or [])
    if words:
        return words

    cells = read_plan_voiceover_cells(project, frame_numbers)
    if not any(t.strip() for _, t in cells):
        raise RuntimeError("не удалось прочитать текст кадров из project.xlsx (лист «план», строка 49)")

    from app.services.media_probe import probe_duration

    master_dur = await probe_duration(audio_path)

    if not asr_available():
        logger.warning(
            "[#{}] ASR недоступен ({}) — пропорциональное сопоставление слов по длительности {:.2f}s",
            project.id,
            settings.asr_backend,
            master_dur,
        )
        words = build_proportional_words_for_cells(cells, master_dur)
    else:
        logger.info(
            "[#{}] montage ASR (subs only): {} → {}",
            project.id,
            get_asr_backend_label(),
            audio_path.name,
        )
        asr_path = await prepare_audio_for_asr(audio_path)
        words = await asyncio.to_thread(transcribe_words, asr_path, language="ru")

    if not words:
        logger.warning(
            "[#{}] ASR не вернул слова — fallback на пропорциональные слова",
            project.id,
        )
        words = build_proportional_words_for_cells(cells, master_dur)

    if not words:
        raise RuntimeError("ASR не вернул слова — проверьте voice_full.mp3")

    words_path = audio_dir / f"words_{uuid.uuid4().hex[:8]}.json"
    dump_words_json(words, words_path)
    art_uuid = uuid.uuid4().hex
    session.add(
        Artifact(
            project_id=project.id,
            kind=ArtifactKind.whisper_words,
            uuid=art_uuid,
            path=str(words_path),
            meta={"source": get_asr_backend_label() if asr_available() else "proportional_fallback", "montage": True, "subs_only": True},
        )
    )
    from app.services.asr_words_store import replace_project_asr_words

    await replace_project_asr_words(
        session,
        project.id,
        words,
        backend=active_asr_backend() if asr_available() else "proportional",
        artifact_uuid=art_uuid,
    )
    await session.flush()
    return words
