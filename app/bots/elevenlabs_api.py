"""ElevenLabs TTS через HTTP API — без Chrome и Playwright (п.25 Wave 4).

## Зачем

Озвучка была единственным шагом конвейера, который тянул за собой браузер:
`app/bots/elevenlabs.py` кликал по веб-морде через CDP. Из-за неё для
прогона требовалось живое окно Chrome, залогиненное в ElevenLabs, — то есть
конвейер нельзя было запустить headless, на сервере или из cron.

Технического препятствия не было: тот же ключ `ELEVENLABS_API_KEY` уже
используется для звуковых эффектов (`app/services/sfx_gen.py`), заголовок
тот же `xi-api-key`, эндпоинт соседний.

## Контракт

``POST https://api.elevenlabs.io/v1/text-to-speech/{voice_id}``
заголовки: ``xi-api-key``, ``Content-Type: application/json``
query: ``output_format`` (по умолчанию ``mp3_44100_128``)
тело: ``{"text": …, "model_id": …, "voice_settings": {…}}``
ответ: сырые байты mp3.

## Совместимость

Класс намеренно повторяет сигнатуру ``ElevenLabsBot.tts`` — единственный
реальный вызов в проекте (`app/services/frame_audio.py`) не меняется, шаг
озвучки просто получает другой объект.

## Длинный текст

У API есть лимит на длину запроса. Закадр короткого ролика (60–75 с) — это
около тысячи символов, и укладывается в один запрос. На случай длинных
текстов реализована нарезка по границам предложений и склейка через ffmpeg
(он и так обязателен для сборки).
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import httpx
from loguru import logger

_API_BASE = "https://api.elevenlabs.io/v1"

# Запас к документированному лимиту: режем по предложениям, а не впритык.
_CHUNK_LIMIT = 4000

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?…])\s+")


class ElevenLabsApiError(RuntimeError):
    """Ошибка TTS API (совместима с обработкой шага озвучки)."""


def elevenlabs_api_key() -> str:
    from app.settings import settings

    return (settings.elevenlabs_api_key or "").strip()


def elevenlabs_api_configured() -> bool:
    return bool(elevenlabs_api_key())


def _log_error(kind: str, text: str, *, node: str = "tts") -> None:
    """Тот же журнал, что у CDP-бота, — операторская привычка не ломается."""
    from datetime import datetime

    from app.services.log_paths import errors_log_path

    try:
        path = errors_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.utcnow().isoformat(timespec="seconds")
        with path.open("a", encoding="utf-8") as f:
            f.write(f"{ts}\tbot=elevenlabs_api\tnode={node}\tkind={kind}\t{text}\n")
    except OSError as e:  # noqa: BLE001 — журнал не должен валить озвучку
        logger.warning("11Labs API: не смог записать журнал ошибок: {}", e)


def split_text_for_tts(text: str, limit: int = _CHUNK_LIMIT) -> list[str]:
    """Нарезать текст на куски ≤ limit по границам предложений.

    Предложение длиннее лимита режется по словам — терять текст нельзя,
    а обрывать посреди слова хуже, чем посреди фразы.
    """
    body = (text or "").strip()
    if not body:
        return []
    if len(body) <= limit:
        return [body]

    chunks: list[str] = []
    current = ""
    for sentence in _SENTENCE_SPLIT.split(body):
        piece = sentence.strip()
        if not piece:
            continue
        if len(piece) > limit:
            if current:
                chunks.append(current)
                current = ""
            words = piece.split()
            buf = ""
            for word in words:
                candidate = f"{buf} {word}".strip()
                if len(candidate) > limit:
                    if buf:
                        chunks.append(buf)
                    buf = word
                else:
                    buf = candidate
            if buf:
                current = buf
            continue
        candidate = f"{current} {piece}".strip()
        if len(candidate) > limit:
            chunks.append(current)
            current = piece
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


async def _concat_mp3(parts: list[Path], out_path: Path) -> Path:
    """Склеить куски через ffmpeg concat demuxer (перекодирования нет)."""
    list_file = out_path.parent / f"{out_path.stem}_concat.txt"
    list_file.write_text(
        "\n".join(f"file '{p.resolve().as_posix()}'" for p in parts) + "\n",
        encoding="utf-8",
    )
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(list_file),
        "-c",
        "copy",
        str(out_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _out, err = await proc.communicate()
    list_file.unlink(missing_ok=True)
    if proc.returncode != 0 or not out_path.is_file():
        raise ElevenLabsApiError(f"11Labs API: склейка кусков не удалась: {err.decode()[:300]}")
    return out_path


class ElevenLabsApi:
    """TTS через HTTP. Сигнатура `tts` совместима с `ElevenLabsBot`."""

    def __init__(self) -> None:
        if not elevenlabs_api_configured():
            raise ElevenLabsApiError(
                "ELEVENLABS_API_KEY пуст — задай ключ из https://elevenlabs.io/app/settings/api-keys"
            )

    def _headers(self) -> dict[str, str]:
        return {
            "xi-api-key": elevenlabs_api_key(),
            "Content-Type": "application/json",
            "Accept": "audio/mpeg",
        }

    def _body(self, text: str) -> dict[str, object]:
        from app.settings import settings

        body: dict[str, object] = {
            "text": text,
            "model_id": settings.elevenlabs_tts_model,
        }
        voice_settings: dict[str, float | bool] = {}
        if settings.elevenlabs_stability is not None:
            voice_settings["stability"] = float(settings.elevenlabs_stability)
        if settings.elevenlabs_similarity_boost is not None:
            voice_settings["similarity_boost"] = float(settings.elevenlabs_similarity_boost)
        if voice_settings:
            body["voice_settings"] = voice_settings
        return body

    async def _one_request(
        self,
        client: httpx.AsyncClient,
        text: str,
        voice_id: str,
        out_path: Path,
    ) -> Path:
        from app.settings import settings

        url = f"{_API_BASE}/text-to-speech/{voice_id}"
        resp = await client.post(
            url,
            headers=self._headers(),
            params={"output_format": settings.elevenlabs_output_format},
            json=self._body(text),
        )
        if resp.status_code >= 400:
            detail = resp.text[:300]
            msg = f"11Labs API HTTP {resp.status_code}: {detail}"
            _log_error(kind=f"http_{resp.status_code}", text=msg)
            raise ElevenLabsApiError(msg)
        data = resp.content or b""
        if len(data) < 500:
            msg = f"11Labs API: подозрительно короткий ответ ({len(data)} байт)"
            _log_error(kind="short_audio", text=msg)
            raise ElevenLabsApiError(msg)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(data)
        return out_path

    async def tts(
        self,
        text: str,
        out_path: Path,
        *,
        timeout: float = 300,
        voice_id: str | None = None,
        project_id: int | None = None,
    ) -> Path:
        """Озвучить текст в `out_path` (mp3). Возвращает путь к файлу."""
        from app.bots.elevenlabs import DEFAULT_ELEVENLABS_VOICE_ID
        from app.services.media_ledger import media_call
        from app.settings import settings

        vid = (voice_id or DEFAULT_ELEVENLABS_VOICE_ID).strip()
        chunks = split_text_for_tts(text)
        if not chunks:
            raise ElevenLabsApiError("11Labs API: пустой текст озвучки")

        logger.info(
            "[#{}] 11Labs API: voice_id={} текст {} симв, кусков {}",
            project_id,
            vid,
            len(text or ""),
            len(chunks),
        )

        async with media_call(
            "elevenlabs",
            "tts",
            # Модель — фактическая: ставка за символ у multilingual_v2 вдвое
            # выше, чем у flash/turbo, и она переключается настройкой. Писать
            # сюда «tts» значило бы считать деньги по старому тарифу молча.
            model=settings.elevenlabs_tts_model or "tts",
            units=float(len(text or "")),
            unit="char",
            project_id=project_id,
        ):
            async with httpx.AsyncClient(timeout=float(timeout)) as client:
                if len(chunks) == 1:
                    await self._one_request(client, chunks[0], vid, out_path)
                else:
                    parts: list[Path] = []
                    for i, chunk in enumerate(chunks, start=1):
                        part = out_path.parent / f"{out_path.stem}_part{i:02d}.mp3"
                        await self._one_request(client, chunk, vid, part)
                        parts.append(part)
                    await _concat_mp3(parts, out_path)
                    for part in parts:
                        part.unlink(missing_ok=True)

        size = out_path.stat().st_size if out_path.is_file() else 0
        logger.info("[#{}] 11Labs API: mp3 сохранён → {} ({} байт)", project_id, out_path, size)
        return out_path
