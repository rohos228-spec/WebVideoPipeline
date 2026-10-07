"""REST API для каталога и образцов русских голосов ElevenLabs (WaveSpeed)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from app.services.ru_voices import get_ru_voice_by_id, get_ru_voices, get_sample_path

router = APIRouter(prefix="/voices", tags=["voices"])


@router.get("/catalog")
async def get_voices_catalog(
    gender: str | None = Query(None, description="Фильтр по полу: 'female' или 'male'"),
    search: str | None = Query(None, description="Поиск по имени или описанию"),
) -> list[dict]:
    """Возвращает каталог доступных голосов с возможностью фильтрации."""
    voices = get_ru_voices()
    if gender:
        target_gender = gender.strip().lower()
        voices = [v for v in voices if v.get("gender") == target_gender]
    if search:
        s = search.strip().lower()
        voices = [
            v
            for v in voices
            if s in v.get("name", "").lower()
            or s in v.get("description", "").lower()
            or s in v.get("id", "").lower()
        ]
    return voices


@router.get("/{voice_id}")
async def get_voice_info(voice_id: str) -> dict:
    """Получить информацию о конкретном голосе по его ID."""
    voice = get_ru_voice_by_id(voice_id)
    if not voice:
        raise HTTPException(status_code=404, detail=f"Voice '{voice_id}' not found")
    return voice


@router.get("/{voice_id}/sample")
async def get_voice_sample(voice_id: str) -> FileResponse:
    """Отдаёт MP3-образец голоса для предпрослушивания."""
    path = get_sample_path(voice_id)
    if not path or not path.is_file():
        raise HTTPException(status_code=404, detail=f"Sample for voice '{voice_id}' not found")
    return FileResponse(
        path=path,
        media_type="audio/mpeg",
        headers={"Cache-Control": "public, max-age=86400"},
        filename=f"{voice_id}.mp3",
    )
