"""Neutral wire format for the browser (TZ p.22: no provider names anywhere user-visible).

Internally the backend keeps provider-named ids (``kie:...`` slugs, ``*-vibecode`` model ids,
``provider: "outsee"`` ...). Everything that goes to the browser through ``/api/*`` JSON is
rewritten to neutral codes, and everything that comes back (path, query, JSON body) is mapped
back, so the UI and the bundle never contain provider words while backend logic stays intact.

* strings without whitespace (ids, slugs, keys, enum values): reversible token codes
  kie->zkq, outsee->zoq, vibecode->zvq, elevenlabs->zeq, wavespeed->zwq, 52pay->zpq,
  anthropic->zaq, openai->zgq (case kept: lower / UPPER / Title);
* free text (contains whitespace): provider words become ``VIA`` (display only).

Disable with ``VIA_WIRE_NEUTRAL=0`` (legacy unit tests run with it off, see tests/conftest.py).
"""
from __future__ import annotations

import json
import os
import re
from urllib.parse import parse_qsl, urlencode

_WORDS = {
    "kie": "zkq",
    "outsee": "zoq",
    "vibecode": "zvq",
    "elevenlabs": "zeq",
    "wavespeed": "zwq",
    "52pay": "zpq",
    "anthropic": "zaq",
    "openai": "zgq",
}
_REV = {v: k for k, v in _WORDS.items()}
# word boundary like check_providers (\b, so kie_market / KIE_API_KEY stay untouched)
_FWD_RE = re.compile(r"(?i)\b(" + "|".join(re.escape(w) for w in _WORDS) + r")\b")
_REV_RE = re.compile(r"(?i)\b(" + "|".join(_REV) + r")\b")
_TEXT_RE = re.compile(
    r"(?i)\b(52pay|kie|outsee|vibecode|wavespeed|eleven\s?labs|anthropic|openai)\b(\.io)?"
)

_SKIP_PREFIXES = ("/api/openapi.json", "/api/docs")


def enabled() -> bool:
    return os.environ.get("VIA_WIRE_NEUTRAL", "1").strip().lower() not in ("0", "false", "no", "off")


def _case(src: str, dst: str) -> str:
    if src.isupper() and any(c.isalpha() for c in src):
        return dst.upper()
    if src[:1].isupper():
        return dst[:1].upper() + dst[1:]
    return dst


def encode_str(s: str) -> str:
    if not s or not _FWD_RE.search(s):
        return s
    if s.startswith("data:"):
        return s  # inline payloads stay byte-exact (URLs are coded reversibly like ids)
    if any(ch.isspace() for ch in s):
        return _TEXT_RE.sub("VIA", s)
    return _FWD_RE.sub(lambda m: _case(m.group(1), _WORDS[m.group(1).lower()]), s)


def decode_str(s: str) -> str:
    if not s or not _REV_RE.search(s):
        return s
    return _REV_RE.sub(lambda m: _case(m.group(1), _REV[m.group(1).lower()]), s)


def _walk(obj, fn):
    if isinstance(obj, str):
        return fn(obj)
    if isinstance(obj, list):
        return [_walk(x, fn) for x in obj]
    if isinstance(obj, dict):
        return {(fn(k) if isinstance(k, str) else k): _walk(v, fn) for k, v in obj.items()}
    return obj


def encode_obj(obj):
    return _walk(obj, encode_str)


def decode_obj(obj):
    return _walk(obj, decode_str)


class WireNeutralASGI:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or not enabled():
            return await self.app(scope, receive, send)
        path = scope.get("path", "")
        if path.startswith(_SKIP_PREFIXES):
            return await self.app(scope, receive, send)
        if not path.startswith("/api/"):
            # media / file URLs handed out by the API may carry codes: map the path back only
            new_path = "/".join(decode_str(p) for p in path.split("/"))
            if new_path != path:
                scope = dict(scope)
                scope["path"] = new_path
                scope["raw_path"] = new_path.encode()
            return await self.app(scope, receive, send)

        # ---- request: path + query + JSON body back to internal ids
        new_path = "/".join(decode_str(p) for p in path.split("/"))
        qs = scope.get("query_string", b"") or b""
        new_qs = qs
        if qs and _REV_RE.search(qs.decode("latin-1")):
            pairs = parse_qsl(qs.decode("utf-8", "replace"), keep_blank_values=True)
            new_qs = urlencode([(decode_str(k), decode_str(v)) for k, v in pairs]).encode()
        if new_path != path or new_qs != qs:
            scope = dict(scope)
            scope["path"] = new_path
            scope["raw_path"] = new_path.encode()
            scope["query_string"] = new_qs

        ctype = b""
        for k, v in scope.get("headers", []):
            if k.lower() == b"content-type":
                ctype = v
        recv = receive
        if b"application/json" in ctype.lower():
            chunks = []
            more = True
            while more:
                msg = await receive()
                if msg["type"] != "http.request":
                    break
                chunks.append(msg.get("body", b""))
                more = msg.get("more_body", False)
            body = b"".join(chunks)
            if body and _REV_RE.search(body.decode("utf-8", "replace")):
                try:
                    body = json.dumps(decode_obj(json.loads(body)), ensure_ascii=False).encode("utf-8")
                except ValueError:
                    pass
            hdrs = [(k, v) for k, v in scope.get("headers", []) if k.lower() != b"content-length"]
            hdrs.append((b"content-length", str(len(body)).encode()))
            scope = dict(scope)
            scope["headers"] = hdrs
            sent = {"done": False}

            async def recv():
                if not sent["done"]:
                    sent["done"] = True
                    return {"type": "http.request", "body": body, "more_body": False}
                return await receive()

        # ---- response: buffer JSON and encode
        state = {"start": None, "json": False, "buf": []}

        async def snd(message):
            if message["type"] == "http.response.start":
                ct = b""
                enc = False
                for k, v in message.get("headers", []):
                    if k.lower() == b"content-type":
                        ct = v.lower()
                    elif k.lower() == b"content-encoding":
                        enc = True
                if b"application/json" in ct and not enc:
                    state["start"] = message
                    state["json"] = True
                    return
                return await send(message)
            if message["type"] == "http.response.body" and state["json"]:
                state["buf"].append(message.get("body", b""))
                if message.get("more_body", False):
                    return
                raw = b"".join(state["buf"])
                out = raw
                try:
                    txt = raw.decode("utf-8")
                    if _FWD_RE.search(txt):
                        out = json.dumps(encode_obj(json.loads(txt)), ensure_ascii=False).encode("utf-8")
                except ValueError:
                    pass
                start = state["start"]
                hdrs = [(k, v) for k, v in start.get("headers", []) if k.lower() != b"content-length"]
                hdrs.append((b"content-length", str(len(out)).encode()))
                await send({**start, "headers": hdrs})
                return await send({"type": "http.response.body", "body": out, "more_body": False})
            return await send(message)

        return await self.app(scope, recv, snd)
