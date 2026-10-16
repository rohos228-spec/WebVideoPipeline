"""Бренды сторонних провайдеров не должны попадать в пользовательский текст (ТЗ п. 22).

scrub(text)           — замена имён провайдеров на нейтральные «VIA …».
scrub_obj(obj)        — рекурсивно для dict/list (ответы API, отчёты).
BrandScrubMiddleware  — aiogram: чистит исходящие сообщения Telegram.
ErrorScrubASGI        — ASGI: чистит тела ответов с ошибками (status >= 400) и SSE-уведомления не трогает.
"""
from __future__ import annotations

import contextlib
import json
import re

# Порядок важен: сначала длинные формы.
_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"outsee\.io", re.I), "VIA"),
    (re.compile(r"\b(?:outsee|оутси|аутси)\b", re.I), "VIA Render"),
    (re.compile(r"\bkie(?:\.ai)?\b|\bкие\b", re.I), "VIA Render"),
    (re.compile(r"\b52\s?pay\b|\bpay52\b", re.I), "VIA"),
    (re.compile(r"\bvibe\s?code\b", re.I), "VIA"),
    (re.compile(r"\bwavespeed(?:\.ai)?\b", re.I), "VIA Render"),
    (re.compile(r"\beleven\s?labs\b", re.I), "VIA Voice"),
    (re.compile(r"\b(?:anthropic|openai)\b", re.I), "VIA"),
]
_ANY = re.compile(
    r"outsee|оутси|аутси|\bkie\b|\bкие\b|52\s?pay|pay52|vibe\s?code|wavespeed|eleven\s?labs|anthropic|openai",
    re.I,
)
# Переменные окружения с ключами в тексте ошибок: KIE_API_KEY -> API_KEY
_ENV_RX = re.compile(r"\b(?:OUTSEE|KIE|PAY52(?:_[A-Z]+)*|VIBECODE|WAVESPEED|ELEVENLABS)_(API_KEY|TOKEN|KEY)\b")


def has_provider(text: str) -> bool:
    return isinstance(text, str) and bool(_ANY.search(text) or _ENV_RX.search(text))


def scrub(text):
    if not isinstance(text, str) or not text or not has_provider(text):
        return text
    text = _ENV_RX.sub(r"\1", text)
    for rx, rep in _RULES:
        text = rx.sub(rep, text)
    return text


def scrub_obj(obj, depth: int = 0):
    """dict/list/str → копия без имён провайдеров (ключи словарей не трогаем)."""
    if depth > 12:
        return obj
    if isinstance(obj, str):
        return scrub(obj)
    if isinstance(obj, dict):
        return {k: scrub_obj(v, depth + 1) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [scrub_obj(v, depth + 1) for v in obj]
    return obj


def _scrub_tg(obj, depth=0):
    if obj is None or depth > 3:
        return
    for f in ("text", "caption", "question", "title", "description", "message_text"):
        v = getattr(obj, f, None)
        if isinstance(v, str) and has_provider(v):
            with contextlib.suppress(Exception):
                setattr(obj, f, scrub(v))
    for f in ("media", "input_message_content", "results"):
        v = getattr(obj, f, None)
        if isinstance(v, list):
            for it in v:
                _scrub_tg(it, depth + 1)
        elif v is not None and not isinstance(v, (str, bytes)):
            _scrub_tg(v, depth + 1)
    kb = getattr(obj, "reply_markup", None)
    rows = getattr(kb, "inline_keyboard", None) or getattr(kb, "keyboard", None)
    if rows:
        for row in rows:
            for b in row:
                _scrub_tg(b, depth + 1)


_scrub_obj = _scrub_tg  # совместимость со старым именем

try:
    from aiogram.client.session.middlewares.base import BaseRequestMiddleware

    class BrandScrubMiddleware(BaseRequestMiddleware):
        async def __call__(self, make_request, bot, method):
            with contextlib.suppress(Exception):
                _scrub_tg(method)
            return await make_request(bot, method)
except Exception:  # pragma: no cover
    BrandScrubMiddleware = None  # type: ignore


class ErrorScrubASGI:
    """Чистит тела ответов с ошибками (>= 400, JSON/текст). Подключение: app.add_middleware(ErrorScrubASGI)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        start: dict | None = None
        chunks: list[bytes] = []

        async def _send(message):
            nonlocal start
            if message["type"] == "http.response.start":
                if message.get("status", 200) < 400:
                    start = None
                    await send(message)
                    return
                start = message
                return
            if message["type"] == "http.response.body" and start is not None:
                chunks.append(message.get("body", b""))
                if message.get("more_body"):
                    return
                body = b"".join(chunks)
                ctype = next((v for k, v in start.get("headers", []) if k.lower() == b"content-type"), b"")
                if b"json" in ctype or b"text" in ctype:
                    with contextlib.suppress(Exception):
                        txt = body.decode("utf-8")
                        if has_provider(txt):
                            if b"json" in ctype:
                                body = json.dumps(scrub_obj(json.loads(txt)), ensure_ascii=False).encode("utf-8")
                            else:
                                body = scrub(txt).encode("utf-8")
                headers = [(k, v) for k, v in start.get("headers", []) if k.lower() != b"content-length"]
                headers.append((b"content-length", str(len(body)).encode()))
                await send({**start, "headers": headers})
                await send({"type": "http.response.body", "body": body})
                return
            await send(message)

        await self.app(scope, receive, _send)
