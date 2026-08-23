"""Чат студии: сообщение внутрь, лента событий наружу.

`POST /api/chat` — стрим Server-Sent Events. Каждое событие петли уходит
клиенту сразу, а не после того, как модель договорит: вызов инструмента
виден в ленте в момент вызова, результат — в момент результата. Иначе
человек десять секунд смотрит на пустой экран и не знает, живо ли вообще.

**SSE, а не WebSocket.** Разговор односторонний: клиент отправил сообщение,
дальше только слушает. У SSE есть переподключение из коробки и он проходит
через любой прокси; сокет здесь дал бы вторую точку отказа без второй
возможности. Стрим шагов конвейера в проекте уже сделан так же
(`app/web/routers/runtime_streams.py`).

**Сообщение обрабатывается в одной сессии БД от начала до конца.** Инструменты
пишут в базу — заводят проект, откатывают кадр, утверждают карточку, — и
каждая такая запись обязана попасть в ту же транзакцию арендатора, что и
чтение перед ней. Открывать сессию на каждый инструмент значило бы позволить
шагу увидеть половину собственных изменений.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.web.deps import get_session

router = APIRouter(prefix="/chat", tags=["chat"])


class ChatMessage(BaseModel):
    """Одна реплика истории. Роли те же, что у провайдеров."""

    role: str = Field(pattern="^(user|assistant)$")
    content: str


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=8000)
    history: list[ChatMessage] = []


@router.post("")
async def chat_stream(body: ChatRequest, session: AsyncSession = Depends(get_session)) -> EventSourceResponse:
    """Обработать сообщение, отдавая события петли по мере появления."""
    from app.services.studio_agent.loop import run_turn

    history = [{"role": m.role, "content": m.content} for m in body.history]

    async def _events() -> AsyncIterator[dict]:
        try:
            async for event in run_turn(session, body.message, history=history):
                yield {
                    "event": event.type,
                    "data": json.dumps(event.payload, ensure_ascii=False, default=str),
                }
        except Exception as exc:  # noqa: BLE001
            # Падение петли не должно выглядеть как оборванное соединение:
            # клиент переподключится и получит то же самое ещё раз.
            yield {
                "event": "error",
                "data": json.dumps({"error": str(exc)}, ensure_ascii=False),
            }
        yield {"event": "done", "data": "{}"}

    return EventSourceResponse(_events())


@router.get("/tools")
async def list_tools() -> list[dict]:
    """Какие инструменты есть у агента. Для отладки и для карточек в ленте."""
    from app.services.studio_agent.tools import tool_manifest

    return tool_manifest()
