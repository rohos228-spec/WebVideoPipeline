import pytest

from app.models import Project
from app.services.elevenlabs_voices import (
    DEFAULT_ELEVENLABS_VOICE_ID,
    probe_live_voice_ids,
    resolve_elevenlabs_voice_id,
)


def test_resolve_default():
    p = Project(topic="t")
    p.meta = {}
    assert resolve_elevenlabs_voice_id(p) == DEFAULT_ELEVENLABS_VOICE_ID


def test_resolve_from_meta():
    p = Project(topic="t")
    p.meta = {
        "node_step_params": {
            "audio": {"elevenlabs_voice_id": "JBFqnCBsd6RMkjVDRZzb"},
        },
    }
    assert resolve_elevenlabs_voice_id(p) == "JBFqnCBsd6RMkjVDRZzb"


def test_resolve_invalid_raises():
    p = Project(topic="t")
    p.meta = {"node_step_params": {"audio": {"elevenlabs_voice_id": "bad"}}}
    with pytest.raises(ValueError, match="нет в каталоге"):
        resolve_elevenlabs_voice_id(p)


@pytest.mark.asyncio
async def test_probe_live_voice_ids_caches(monkeypatch) -> None:
    import app.services.elevenlabs_voices as voices_mod
    from app import settings as settings_mod

    calls = {"n": 0}

    class FakeResp:
        def raise_for_status(self):
            return None

        def json(self):
            return {"voices": [{"voice_id": "abc"}, {"voice_id": ""}]}

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, *a, **k):
            calls["n"] += 1
            return FakeResp()

    monkeypatch.setattr("httpx.AsyncClient", FakeClient)
    monkeypatch.setattr(settings_mod.settings, "elevenlabs_api_key", "k")

    live1 = await probe_live_voice_ids()
    live2 = await probe_live_voice_ids()
    assert live1 == {"abc"}
    assert live2 == {"abc"}
    assert calls["n"] == 1

    monkeypatch.setattr(settings_mod.settings, "elevenlabs_api_key", "")
    voices_mod._voices_probe_cache["live"] = None
    assert await probe_live_voice_ids() is None
