"""Chat SSE оркестратора: событие-ошибка и пустой стрим — ProviderError, а не ответ «»."""

from __future__ import annotations

import httpx
import pytest

from app.orchestrator.routing.config import ProviderConfig
from app.orchestrator.routing.transport import HttpxTransport, ProviderError, ProviderRequest, parse_chat_sse

_CFG = ProviderConfig(
    name="vibecode",
    api_key_env="VIBECODE_API_KEY",
    base_url_env="VIBECODE_BASE_URL",
    chat_path="/chat/completions",
    api="openai-chat",
)

# Как отвечал vibecode на вопрос о параллели: keep-alive раз в 10 с, затем ошибка без choices.
UPSTREAM_ERROR_SSE = (
    ": keep-alive\n\n"
    ": keep-alive\n\n"
    'data: {"error": {"message": "upstream error", "type": "upstream_error"}}\n\n'
)


def _transport(body: str) -> HttpxTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, text=body)

    return HttpxTransport(
        {"vibecode": _CFG},
        env={"VIBECODE_API_KEY": "vk-test"},
        client_factory=lambda timeout: httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=timeout),
    )


def _request() -> ProviderRequest:
    return ProviderRequest(
        model="gpt-5.6-sol",
        provider="vibecode",
        messages=[{"role": "user", "content": "сколько проектов можно крутить одновременно"}],
        temperature=None,
        max_tokens=None,
        reasoning_effort="high",
        timeout_s=30,
        stream=True,
    )


async def _send(body: str):
    transport = _transport(body)
    request = _request()
    return await transport._send(request, _CFG, "https://vibecode.test/v1/chat/completions", "vk-test")


@pytest.mark.asyncio
async def test_upstream_error_event_raises_not_billable_not_retryable() -> None:
    with pytest.raises(ProviderError, match="upstream error") as caught:
        await _send(UPSTREAM_ERROR_SSE)
    assert caught.value.retryable is False
    assert caught.value.billable is False


@pytest.mark.asyncio
async def test_empty_stream_with_done_raises_retryable() -> None:
    body = 'data: {"choices":[{"delta":{"role":"assistant"}}]}\n\ndata: [DONE]\n\n'
    with pytest.raises(ProviderError, match="empty chat stream") as caught:
        await _send(body)
    assert caught.value.retryable is True
    assert caught.value.billable is True


@pytest.mark.asyncio
async def test_text_survives_error_event_in_the_middle() -> None:
    body = (
        'data: {"code": 502, "msg": "upstream_error"}\n\n'
        'data: {"choices":[{"delta":{"content":"до 10 "}}]}\n\n'
        'data: {"choices":[{"delta":{"content":"проектов"},"finish_reason":"stop"}]}\n\n'
        "data: [DONE]\n\n"
    )
    result = await _send(body)
    assert result.text == "до 10 проектов"
    assert result.finish_reason == "stop"


@pytest.mark.asyncio
async def test_normal_stream_unchanged() -> None:
    body = (
        'data: {"choices":[{"delta":{"content":"ok"},"finish_reason":"stop"}]}\n\n'
        'data: {"choices":[],"usage":{"prompt_tokens":5,"completion_tokens":1}}\n\n'
        "data: [DONE]\n\n"
    )
    result = await _send(body)
    assert result.text == "ok"
    assert (result.prompt_tokens, result.completion_tokens) == (5, 1)


def test_parse_chat_sse_only_error_raises() -> None:
    with pytest.raises(ProviderError, match="upstream error"):
        parse_chat_sse(UPSTREAM_ERROR_SSE)
