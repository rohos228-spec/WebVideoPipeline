"""Browser wire codec: provider ids are coded on the way out and decoded on the way in."""
import re

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.web import wire_neutral as wn

PROVIDER_RE = re.compile(r"(?i)\b(52pay|kie|outsee|vibecode|wavespeed|elevenlabs|anthropic|openai)\b")


def _app():
    app = FastAPI()
    seen = {}

    @app.get("/api/models")
    def models():
        return {"models": [{"id": "gpt-5.5-vibecode", "provider": "kie", "group": "Anthropic",
                            "slug": "kie:flux-2-pro", "hint": "Kie Market error", "env": "KIE_API_KEY",
                            "icon": "https://cdn.example/x.webp"}]}

    @app.post("/api/echo/{mid}")
    async def echo(mid: str, request: Request, q: str = ""):
        seen["mid"], seen["q"], seen["body"] = mid, q, await request.json()
        return {"ok": True}

    app.add_middleware(wn.WireNeutralASGI)
    return app, seen


def test_roundtrip(monkeypatch):
    monkeypatch.setenv("VIA_WIRE_NEUTRAL", "1")
    app, seen = _app()
    c = TestClient(app)
    r = c.get("/api/models")
    assert not PROVIDER_RE.search(r.text), r.text
    m = r.json()["models"][0]
    assert m["id"] == "gpt-5.5-zvq" and m["slug"] == "zkq:flux-2-pro" and m["provider"] == "zkq"
    assert m["hint"] == "VIA Market error" and m["env"] == "KIE_API_KEY"
    assert m["icon"] == "https://cdn.example/x.webp"
    c.post("/api/echo/" + m["id"], params={"q": m["slug"]}, json={"provider": m["provider"], "group": m["group"]})
    assert seen == {"mid": "gpt-5.5-vibecode", "q": "kie:flux-2-pro",
                    "body": {"provider": "kie", "group": "Anthropic"}}


def test_off(monkeypatch):
    monkeypatch.setenv("VIA_WIRE_NEUTRAL", "0")
    app, _ = _app()
    assert "vibecode" in TestClient(app).get("/api/models").text


def test_real_app_text_llm_clean(client):
    r = client.get("/api/text-llm")
    if r.status_code == 200:
        assert not PROVIDER_RE.search(r.text), r.text[:500]
