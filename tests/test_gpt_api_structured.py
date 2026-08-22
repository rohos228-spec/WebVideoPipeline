"""Этап 5: structured outputs в транспорте (ResponseSchema, контрактный режим).

Покрывает: прикрепление схемы по режиму/релею, served-model детект,
отключение continuation в контрактном режиме, наследование схемы
volume-добором и адаптивным дроблением.
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.services import gpt_api
from app.services.gpt_api import (
    GptApiError,
    GptChatResult,
    ResponseSchema,
    _check_served_model,
    _structured_outputs_active,
    chat,
)

SCHEMA = ResponseSchema(
    name="apply_ops_test",
    schema={
        "type": "object",
        "properties": {"ops": {"type": "array"}},
        "required": ["ops"],
        "additionalProperties": False,
    },
)


def _enable(monkeypatch, *, relays: str = "gw.test", mode: str = "auto") -> None:
    from app.settings import settings

    monkeypatch.setattr(settings, "text_llm_provider", "kie")
    monkeypatch.setattr(settings, "tokenrouter_api_key", "")
    monkeypatch.setattr(settings, "gpt_api_key", "test-key")
    monkeypatch.setattr(settings, "gpt_base_url", "https://gw.test")
    monkeypatch.setattr(settings, "gpt_chat_path", "/v1/chat/completions")
    monkeypatch.setattr(settings, "gpt_api_mode", "chat")
    monkeypatch.setattr(settings, "gpt_max_retries", 0)
    monkeypatch.setattr(settings, "gpt_proxy_url", None)
    monkeypatch.setattr(settings, "gpt_structured_outputs", mode)
    monkeypatch.setattr(settings, "gpt_structured_relays", relays)
    monkeypatch.setattr(gpt_api, "_PROXY_LOGGED", False)


def _mock_httpx(monkeypatch, handler) -> None:
    real_client = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs.pop("proxy", None)
        kwargs["transport"] = httpx.MockTransport(handler)
        kwargs["trust_env"] = False
        return real_client(*args, **kwargs)

    monkeypatch.setattr(gpt_api.httpx, "AsyncClient", factory)

    async def _no_sleep(*_a, **_k):
        return None

    monkeypatch.setattr(gpt_api.asyncio, "sleep", _no_sleep)


def _completion(text: str, *, model: str = "gpt-5.6-sol") -> dict:
    return {
        "model": model,
        "choices": [{"message": {"content": text}, "finish_reason": "stop"}],
        "usage": {"total_tokens": 10},
    }


# ── _structured_outputs_active ───────────────────────────────────────────


def test_active_modes(monkeypatch) -> None:
    _enable(monkeypatch, relays="chattiq.ru")
    assert _structured_outputs_active("https://chattiq.ru/v1/chat/completions")
    assert not _structured_outputs_active("https://gw.test/v1/chat/completions")

    _enable(monkeypatch, mode="on", relays="")
    assert _structured_outputs_active("https://anything.example")

    _enable(monkeypatch, mode="off", relays="chattiq.ru")
    assert not _structured_outputs_active("https://chattiq.ru/v1")


# ── прикрепление схемы к body ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_schema_attached_on_enforcing_relay(monkeypatch) -> None:
    _enable(monkeypatch, relays="gw.test")
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json=_completion('{"ops":[]}'))

    _mock_httpx(monkeypatch, handler)
    result = await chat(prompt="p", auto_pack=False, model="gpt-5.6-sol", response_schema=SCHEMA)
    assert result.text == '{"ops":[]}'
    rf = bodies[0]["response_format"]
    assert rf["type"] == "json_schema"
    assert rf["json_schema"]["name"] == "apply_ops_test"
    assert rf["json_schema"]["strict"] is True
    assert rf["json_schema"]["schema"] == SCHEMA.schema


@pytest.mark.asyncio
async def test_schema_not_attached_on_unverified_relay(monkeypatch) -> None:
    _enable(monkeypatch, relays="chattiq.ru")  # gw.test не в списке enforces
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json=_completion('{"ops":[]}'))

    _mock_httpx(monkeypatch, handler)
    result = await chat(prompt="p", auto_pack=False, model="gpt-5.6-sol", response_schema=SCHEMA)
    assert result.text == '{"ops":[]}'
    assert "response_format" not in bodies[0]  # деградация: без параметра


@pytest.mark.asyncio
async def test_no_schema_no_param(monkeypatch) -> None:
    _enable(monkeypatch, mode="on")
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json=_completion("ok"))

    _mock_httpx(monkeypatch, handler)
    await chat(prompt="p", auto_pack=False, model="m")
    assert "response_format" not in bodies[0]


# ── served-model детект ──────────────────────────────────────────────────


def test_served_model_norm_match() -> None:
    # kie "gpt-5-6-sol" vs OpenAI "gpt-5.6-sol" — одна модель после нормализации
    r = GptChatResult(text="", model="gpt-5-6-sol", served_model="gpt-5.6-sol")
    _check_served_model(r, use_model="gpt-5-6-sol", contract_active=True)


def test_served_model_mismatch_raises() -> None:
    r = GptChatResult(text="", model="gpt-5.6-sol", served_model="MiniMax-M3")
    with pytest.raises(GptApiError) as ei:
        _check_served_model(r, use_model="gpt-5.6-sol", contract_active=True)
    assert ei.value.context.get("error_kind") == "model_mismatch"
    assert ei.value.retryable


def test_served_model_ignored_without_contract() -> None:
    r = GptChatResult(text="", model="gpt-5.6-sol", served_model="MiniMax-M3")
    _check_served_model(r, use_model="gpt-5.6-sol", contract_active=False)


@pytest.mark.asyncio
async def test_chat_mismatch_end_to_end(monkeypatch) -> None:
    _enable(monkeypatch, relays="gw.test")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_completion('{"ops":[]}', model="MiniMax-M3"))

    _mock_httpx(monkeypatch, handler)
    with pytest.raises(GptApiError) as ei:
        await chat(
            prompt="p",
            auto_pack=False,
            model="gpt-5.6-sol",
            response_schema=SCHEMA,
        )
    assert ei.value.context.get("error_kind") == "model_mismatch"


# ── continuation выключен в контрактном режиме (vibecode-ветка) ──────────


def _sse_completions(text: str, *, model: str = "gpt-5.6-sol") -> str:
    chunk = {
        "id": "c1",
        "model": model,
        "choices": [{"delta": {"content": text}, "finish_reason": "stop"}],
        "usage": {"total_tokens": 5},
    }
    return f"data: {json.dumps(chunk, ensure_ascii=False)}\n\ndata: [DONE]\n\n"


def _enable_vibecode(monkeypatch) -> None:
    from app.settings import settings

    monkeypatch.setattr(settings, "text_llm_provider", "vibecode")
    monkeypatch.setattr(settings, "vibecode_api_key", "vk-test")
    monkeypatch.setattr(settings, "vibecode_base_url", "https://vibe.test/v1")
    monkeypatch.setattr(settings, "gpt_max_retries", 0)
    monkeypatch.setattr(settings, "gpt_proxy_url", None)
    monkeypatch.setattr(settings, "gpt_structured_outputs", "auto")
    monkeypatch.setattr(settings, "gpt_structured_relays", "vibe.test")
    monkeypatch.setattr(gpt_api, "_PROXY_LOGGED", False)


@pytest.mark.asyncio
async def test_truncated_contract_no_continuation(monkeypatch) -> None:
    _enable_vibecode(monkeypatch)
    calls: list[httpx.Request] = []
    truncated = '{"ops":[{"frame_uuid":"a1","fields":{"x":"незакрытый'

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            text=_sse_completions(truncated),
        )

    _mock_httpx(monkeypatch, handler)
    with pytest.raises(GptApiError) as ei:
        await chat(
            prompt="p",
            auto_pack=False,
            model="gpt-5.6-sol",
            response_schema=SCHEMA,
        )
    assert ei.value.context.get("error_kind") == "truncated_contract"
    assert len(calls) == 1  # continuation-запросов не было


@pytest.mark.asyncio
async def test_truncated_without_contract_still_continues(monkeypatch) -> None:
    _enable_vibecode(monkeypatch)
    calls: list[httpx.Request] = []
    truncated = '{"ops":[{"frame_uuid":"a1","fields":{"x":"незакрытый'

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                text=_sse_completions(truncated),
            )
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            text=_sse_completions('"}}]}'),
        )

    _mock_httpx(monkeypatch, handler)
    result = await chat(prompt="p", auto_pack=False, model="gpt-5.6-sol")
    assert len(calls) >= 2  # continuation сработал (старое поведение)
    assert result.finish_reason == "stream_continued"


@pytest.mark.asyncio
async def test_truncated_contract_retry_then_success(monkeypatch) -> None:
    """Обрыв в контрактном режиме → ретрай целого вызова → успех."""
    from app.settings import settings

    _enable_vibecode(monkeypatch)
    monkeypatch.setattr(settings, "gpt_max_retries", 1)
    calls: list[httpx.Request] = []
    truncated = '{"ops":[{"frame_uuid":"a1","fields":{"x":"незакрытый'

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        text = truncated if len(calls) == 1 else '{"ops":[]}'
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            text=_sse_completions(text),
        )

    _mock_httpx(monkeypatch, handler)
    result = await chat(prompt="p", auto_pack=False, model="gpt-5.6-sol", response_schema=SCHEMA)
    assert len(calls) == 2
    assert result.text == '{"ops":[]}'


def _sse_responses(text: str, *, model: str = "gpt-5-6-sol") -> str:
    done = {"type": "response.output_text.done", "text": text}
    completed = {
        "type": "response.completed",
        "response": {
            "id": "resp_1",
            "status": "completed",
            "model": model,
            "usage": {"total_tokens": 7},
            "output": [{"content": [{"type": "output_text", "text": text}]}],
        },
    }
    return (
        f"data: {json.dumps(done, ensure_ascii=False)}\n\n"
        f"data: {json.dumps(completed, ensure_ascii=False)}\n\n"
    )


@pytest.mark.asyncio
async def test_responses_mode_attaches_text_format(monkeypatch) -> None:
    """responses-ветка: схема уходит в text.format (не response_format)."""
    from app.settings import settings

    _enable(monkeypatch, relays="gw.test")
    monkeypatch.setattr(settings, "gpt_chat_path", "/codex/v1/responses")
    monkeypatch.setattr(settings, "gpt_api_mode", "auto")
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            text=_sse_responses('{"ops":[]}'),
        )

    _mock_httpx(monkeypatch, handler)
    result = await chat(prompt="p", auto_pack=False, model="gpt-5-6-sol", response_schema=SCHEMA)
    fmt = bodies[0]["text"]["format"]
    assert fmt["type"] == "json_schema"
    assert fmt["schema"] == SCHEMA.schema
    assert "response_format" not in bodies[0]
    assert result.text == '{"ops":[]}'
    assert result.served_model == "gpt-5-6-sol"


def test_served_model_suffix_downgrade_detected() -> None:
    # Подстрочное сравнение пропускало суффикс-вариант (панель)
    r = GptChatResult(text="", model="gpt-4.1", served_model="gpt-4.1-mini")
    with pytest.raises(GptApiError):
        _check_served_model(r, use_model="gpt-4.1", contract_active=True)


def test_served_model_provider_prefix_ok() -> None:
    r = GptChatResult(text="", model="gpt-5.6-sol", served_model="openai/gpt-5.6-sol")
    _check_served_model(r, use_model="gpt-5.6-sol", contract_active=True)


# ── наследование схемы: volume-добор и адаптивное дробление ──────────────


@pytest.mark.asyncio
async def test_volume_complete_inherits_schema(monkeypatch) -> None:
    from app.services import volume_batches

    captured: dict = {}

    async def fake_volume(reply_text, **kwargs):
        captured.update(kwargs)
        return reply_text, False

    monkeypatch.setattr(volume_batches, "volume_complete_apply_ops_reply", fake_volume)
    result = GptChatResult(text='{"ops":[]}', model="m")
    out = await gpt_api._maybe_volume_complete_chat_result(
        result,
        prompt="p",
        accompanying="",
        input_paths=None,
        system=None,
        history=None,
        model="m",
        temperature=None,
        timeout=10.0,
        xlsx_write_contract="apply_ops",
        volume_complete=True,
        response_schema=SCHEMA,
    )
    assert out is result
    assert captured["response_schema"] is SCHEMA


@pytest.mark.asyncio
async def test_auto_pack_passes_schema(monkeypatch) -> None:
    _enable(monkeypatch, relays="gw.test")
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json=_completion('{"ops":[]}'))

    _mock_httpx(monkeypatch, handler)
    await chat(prompt="p", model="gpt-5.6-sol", response_schema=SCHEMA)
    assert bodies and "response_format" in bodies[0]
