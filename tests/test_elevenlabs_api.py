"""ElevenLabs TTS через HTTP API (п.25 Wave 4).

Озвучка была единственным шагом, который тянул за собой Chrome: бот кликал
по веб-морде через CDP, поэтому конвейер нельзя было запустить headless.
Ключ и заголовок те же, что у SFX (`sfx_gen` уже ходит в
`/v1/sound-effects`), эндпоинт соседний.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from app.bots import elevenlabs_api as ea
from app.settings import settings

_MP3 = b"\xff\xfb" + b"\x00" * 4000


@pytest.fixture(autouse=True)
def _key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "elevenlabs_api_key", "test-key")
    monkeypatch.setattr(settings, "elevenlabs_tts_model", "eleven_multilingual_v2")
    monkeypatch.setattr(settings, "elevenlabs_output_format", "mp3_44100_128")
    monkeypatch.setattr(settings, "elevenlabs_stability", None)
    monkeypatch.setattr(settings, "elevenlabs_similarity_boost", None)


# Настоящий класс запоминаем ДО патча: monkeypatch подменяет атрибут самого
# модуля httpx, и фабрика, сославшись на httpx.AsyncClient, создала бы себя.
_REAL_ASYNC_CLIENT = httpx.AsyncClient


def _client_returning(captured: list[httpx.Request], *, status: int = 200, body: bytes = _MP3):
    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(status, content=body)

    class _Factory:
        def __init__(self, *_a, **_k) -> None:
            self._client = _REAL_ASYNC_CLIENT(transport=httpx.MockTransport(handler))

        async def __aenter__(self) -> httpx.AsyncClient:
            return await self._client.__aenter__()

        async def __aexit__(self, *a) -> None:
            await self._client.__aexit__(*a)

    return _Factory


# ── нарезка текста ────────────────────────────────────────────────────────


def test_short_text_is_one_chunk() -> None:
    assert ea.split_text_for_tts("Одно предложение.") == ["Одно предложение."]


def test_empty_text_gives_no_chunks() -> None:
    assert ea.split_text_for_tts("   ") == []


def test_split_keeps_sentence_boundaries() -> None:
    text = ("Первое предложение. " * 40).strip()
    chunks = ea.split_text_for_tts(text, limit=200)
    assert len(chunks) > 1
    for c in chunks:
        assert len(c) <= 200
        assert c.endswith(".")


def test_split_loses_nothing() -> None:
    """Склейка кусков даёт исходный текст по словам — озвучка не теряет речь."""
    text = ("Слово раз два три четыре пять. " * 30).strip()
    chunks = ea.split_text_for_tts(text, limit=150)
    assert " ".join(chunks).split() == text.split()


def test_oversized_sentence_is_split_by_words() -> None:
    """Предложение длиннее лимита режется по словам, а не по символам."""
    text = "слово " * 200
    chunks = ea.split_text_for_tts(text, limit=100)
    assert chunks
    for c in chunks:
        assert len(c) <= 100
    assert " ".join(chunks).split() == text.split()


# ── HTTP-контракт ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_tts_writes_mp3_and_hits_right_endpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: list[httpx.Request] = []
    monkeypatch.setattr(ea.httpx, "AsyncClient", _client_returning(captured))

    out = tmp_path / "voice.mp3"
    got = await ea.ElevenLabsApi().tts("Привет, мир.", out, voice_id="VOICE123", project_id=7)

    assert got == out
    assert out.read_bytes() == _MP3

    req = captured[0]
    assert req.url.path == "/v1/text-to-speech/VOICE123"
    assert req.url.params["output_format"] == "mp3_44100_128"
    assert req.headers["xi-api-key"] == "test-key"
    import json

    body = json.loads(req.content)
    assert body["text"] == "Привет, мир."
    assert body["model_id"] == "eleven_multilingual_v2"
    # voice_settings не шлём, если ничего не задано — пусть решает пресет голоса
    assert "voice_settings" not in body


@pytest.mark.asyncio
async def test_voice_settings_sent_when_configured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "elevenlabs_stability", 0.4)
    monkeypatch.setattr(settings, "elevenlabs_similarity_boost", 0.8)
    captured: list[httpx.Request] = []
    monkeypatch.setattr(ea.httpx, "AsyncClient", _client_returning(captured))

    await ea.ElevenLabsApi().tts("Текст.", tmp_path / "v.mp3", voice_id="V")

    import json

    body = json.loads(captured[0].content)
    assert body["voice_settings"] == {"stability": 0.4, "similarity_boost": 0.8}


@pytest.mark.asyncio
async def test_http_error_is_loud(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """401 — это ошибка шага, а не тихий пустой файл."""
    monkeypatch.setattr(ea.httpx, "AsyncClient", _client_returning([], status=401, body=b"unauthorized"))
    with pytest.raises(ea.ElevenLabsApiError, match="401"):
        await ea.ElevenLabsApi().tts("Текст.", tmp_path / "v.mp3", voice_id="V")
    assert not (tmp_path / "v.mp3").exists()


@pytest.mark.asyncio
async def test_suspiciously_short_audio_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Ответ 200 с огрызком вместо mp3 — брак, а не «озвучка готова»."""
    monkeypatch.setattr(ea.httpx, "AsyncClient", _client_returning([], body=b"nope"))
    with pytest.raises(ea.ElevenLabsApiError, match="короткий ответ"):
        await ea.ElevenLabsApi().tts("Текст.", tmp_path / "v.mp3", voice_id="V")


@pytest.mark.asyncio
async def test_empty_text_rejected(tmp_path: Path) -> None:
    with pytest.raises(ea.ElevenLabsApiError, match="пустой текст"):
        await ea.ElevenLabsApi().tts("   ", tmp_path / "v.mp3", voice_id="V")


def test_no_key_means_no_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "elevenlabs_api_key", "")
    assert ea.elevenlabs_api_configured() is False
    with pytest.raises(ea.ElevenLabsApiError, match="ELEVENLABS_API_KEY"):
        ea.ElevenLabsApi()


@pytest.mark.asyncio
async def test_tts_is_recorded_in_media_ledger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Озвучка — платная генерация: должна попадать в media_calls."""
    monkeypatch.setattr(ea.httpx, "AsyncClient", _client_returning([]))
    recorded: list[dict] = []

    async def fake_record(**kw):
        recorded.append(kw)
        return 1

    monkeypatch.setattr("app.services.media_ledger.record", fake_record)

    await ea.ElevenLabsApi().tts("Привет.", tmp_path / "v.mp3", voice_id="V", project_id=3)

    assert recorded, "вызов не записан в media_ledger"
    row = recorded[0]
    assert row["provider"] == "elevenlabs"
    assert row["kind"] == "tts"
    assert row["unit"] == "char"
    assert row["units"] == float(len("Привет."))
    assert row["result"] == "ok"
