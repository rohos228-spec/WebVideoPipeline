"""Заглушка Bot для работы пайплайна без Telegram."""

from __future__ import annotations

from typing import Any


class _FakeMessage:
    message_id = 0
    chat = None


class NoopBot:
    """Минимальная совместимость с вызовами bot.send_* в шагах."""

    async def send_message(self, *args: Any, **kwargs: Any) -> _FakeMessage:
        return _FakeMessage()

    async def send_photo(self, *args: Any, **kwargs: Any) -> _FakeMessage:
        return _FakeMessage()

    async def send_document(self, *args: Any, **kwargs: Any) -> _FakeMessage:
        return _FakeMessage()

    async def send_video(self, *args: Any, **kwargs: Any) -> _FakeMessage:
        return _FakeMessage()

    async def edit_message_reply_markup(self, *args: Any, **kwargs: Any) -> None:
        return None

    async def edit_message_caption(self, *args: Any, **kwargs: Any) -> None:
        return None

    async def edit_message_text(self, *args: Any, **kwargs: Any) -> None:
        return None

    class session:
        @staticmethod
        async def close() -> None:
            return None


_noop_singleton = NoopBot()


def get_worker_bot(real_bot: Any = None) -> NoopBot:
    """Bot для воркера (no-op в веб-режиме)."""
    return _noop_singleton
