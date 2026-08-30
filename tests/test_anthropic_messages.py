"""Claude через vibecode: транспорт /v1/messages и его подключение в gpt_api.

vibecode отдаёт claude-* только в формате Anthropic — запись в каталоге без
транспорта дала бы 400 на каждый вызов оркестратора.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import anthropic
import httpx2
import pytest

from app.services import anthropic_messages as am
from app.services.gpt_api import GptApiError, GptChatResult, ResponseSchema

SCHEMA = ResponseSchema(name="reply", schema={"type": "object", "properties": {"say": {"type": "string"}}})


def test_base_url_strips_v1_tail() -> None:
    assert am.messages_base_url("https://vibecode.moe/v1") == "https://vibecode.moe"
    assert am.messages_base_url("https://vibecode.moe/v1/") == "https://vibecode.moe"
    assert am.messages_url("https://vibecode.moe/v1") == "https://vibecode.moe/v1/messages"


def test_is_anthropic_model() -> None:
    assert am.is_anthropic_model("claude-opus-5")
    assert am.is_anthropic_model(" Claude-Sonnet-5 ")
    assert not am.is_anthropic_model("gpt-5.6-sol")
    assert not am.is_anthropic_model(None)


def test_convert_messages_system_images_and_seed() -> None:
    system, msgs = am.convert_messages(
        [
            {"role": "system", "content": "S1"},
            {"role": "assistant", "content": "prev"},
            {"role": "system", "content": "S2"},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "hi"},
                    {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,QUJD"}},
                    {"type": "image_url", "image_url": {"url": "https://x/y.png"}},
                ],
            },
        ]
    )
    assert system == "S1\n\nS2"
    # история начиналась с ответа ассистента — подставлена затравка user
    assert msgs[0] == {"role": "user", "content": "(начало диалога)"}
    assert msgs[1] == {"role": "assistant", "content": "prev"}
    blocks = msgs[2]["content"]
    assert blocks[0] == {"type": "text", "text": "hi"}
    assert blocks[1] == {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/jpeg", "data": "QUJD"},
    }
    assert blocks[2] == {"type": "image", "source": {"type": "url", "url": "https://x/y.png"}}


def test_build_request_drops_temperature_and_gates_schema() -> None:
    body = {
        "model": "claude-opus-5",
        "temperature": 0.2,
        "response_format": {"type": "json_schema"},
        "messages": [{"role": "system", "content": "S"}, {"role": "user", "content": "u"}],
    }
    req = am.build_request(body, response_schema=SCHEMA, structured=False)
    assert req["model"] == "claude-opus-5"
    assert req["system"] == "S"
    assert req["messages"] == [{"role": "user", "content": "u"}]
    assert "temperature" not in req and "response_format" not in req
    assert "output_config" not in req
    assert req["max_tokens"] == am.MAX_TOKENS

    req2 = am.build_request(body, response_schema=SCHEMA, structured=True)
    assert req2["output_config"] == {"format": {"type": "json_schema", "schema": SCHEMA.schema}}


def test_usage_dict_covers_both_ledger_branches() -> None:
    u = am.usage_dict(SimpleNamespace(input_tokens=10, output_tokens=5, cache_read_input_tokens=3))
    assert u["prompt_tokens"] == 10 and u["input_tokens"] == 10
    assert u["completion_tokens"] == 5 and u["output_tokens"] == 5
    assert u["total_tokens"] == 15
    assert u["cache_read_input_tokens"] == 3
    assert am.usage_dict(None) == {}


class _FakeStream:
    def __init__(self, msg: Any) -> None:
        self._msg = msg

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get_final_message(self):
        if isinstance(self._msg, BaseException):
            raise self._msg
        return self._msg


class _FakeClient:
    def __init__(self, msg: Any, seen: dict[str, Any]) -> None:
        self._msg = msg
        self._seen = seen
        self.messages = self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def stream(self, **req):
        self._seen.update(req)
        return _FakeStream(self._msg)


def _msg(text: str, *, stop: str = "end_turn", model: str = "claude-opus-5") -> SimpleNamespace:
    return SimpleNamespace(
        id="msg_1",
        model=model,
        stop_reason=stop,
        stop_details=None,
        content=[SimpleNamespace(type="text", text=text)],
        usage=SimpleNamespace(input_tokens=7, output_tokens=3),
    )


def _install(monkeypatch: pytest.MonkeyPatch, msg: Any) -> dict[str, Any]:
    seen: dict[str, Any] = {}
    monkeypatch.setattr(am, "_client", lambda **kw: _FakeClient(msg, seen))
    return seen


@pytest.mark.asyncio
async def test_chat_messages_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _install(monkeypatch, _msg('{"say":"x"}'))
    body = {"model": "claude-opus-5", "messages": [{"role": "user", "content": "u"}]}
    r = await am.chat_messages(
        base_url="https://vibecode.moe/v1", api_key="vk", body=body, timeout=5, use_model="claude-opus-5"
    )
    assert r.text == '{"say":"x"}'
    assert r.finish_reason == "stop"
    assert r.served_model == "claude-opus-5" and r.response_id == "msg_1"
    assert r.usage["prompt_tokens"] == 7 and r.usage["completion_tokens"] == 3
    assert r.raw["transport"] == "anthropic_messages"
    assert seen["model"] == "claude-opus-5" and seen["messages"] == [{"role": "user", "content": "u"}]


@pytest.mark.asyncio
async def test_chat_messages_max_tokens_is_length(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, _msg("partial", stop="max_tokens"))
    r = await am.chat_messages(
        base_url="https://vibecode.moe/v1",
        api_key="vk",
        body={"model": "claude-opus-5", "messages": [{"role": "user", "content": "u"}]},
        timeout=5,
        use_model="claude-opus-5",
    )
    assert r.finish_reason == "length"


@pytest.mark.asyncio
async def test_chat_messages_refusal_is_fatal(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, _msg("", stop="refusal"))
    with pytest.raises(GptApiError) as ei:
        await am.chat_messages(
            base_url="https://vibecode.moe/v1",
            api_key="vk",
            body={"model": "claude-opus-5", "messages": [{"role": "user", "content": "u"}]},
            timeout=5,
            use_model="claude-opus-5",
        )
    assert ei.value.context.get("error_kind") == "refusal"
    assert ei.value.retryable is False


@pytest.mark.asyncio
async def test_chat_messages_empty_is_retryable(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, _msg("   "))
    with pytest.raises(GptApiError) as ei:
        await am.chat_messages(
            base_url="https://vibecode.moe/v1",
            api_key="vk",
            body={"model": "claude-opus-5", "messages": [{"role": "user", "content": "u"}]},
            timeout=5,
            use_model="claude-opus-5",
        )
    assert ei.value.context.get("error_kind") == "empty_stream"
    assert ei.value.retryable is True


def _status_error(status: int, headers: dict[str, str] | None = None) -> anthropic.APIStatusError:
    req = httpx2.Request("POST", "https://vibecode.moe/v1/messages")
    resp = httpx2.Response(status, headers=headers or {}, request=req, text='{"error":"x"}')
    return anthropic.APIStatusError("boom", response=resp, body={"error": "x"})


@pytest.mark.asyncio
async def test_chat_messages_429_retryable_with_retry_after(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, _status_error(429, {"retry-after": "7"}))
    with pytest.raises(GptApiError) as ei:
        await am.chat_messages(
            base_url="https://vibecode.moe/v1",
            api_key="vk",
            body={"model": "claude-opus-5", "messages": [{"role": "user", "content": "u"}]},
            timeout=5,
            use_model="claude-opus-5",
        )
    assert ei.value.retryable is True
    assert ei.value.context.get("status_code") == 429
    assert ei.value.context.get("retry_after_s") == 7


@pytest.mark.asyncio
async def test_chat_messages_400_fatal(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, _status_error(400))
    with pytest.raises(GptApiError) as ei:
        await am.chat_messages(
            base_url="https://vibecode.moe/v1",
            api_key="vk",
            body={"model": "claude-opus-5", "messages": [{"role": "user", "content": "u"}]},
            timeout=5,
            use_model="claude-opus-5",
        )
    assert ei.value.retryable is False


@pytest.mark.asyncio
async def test_chat_messages_connection_error_retryable(monkeypatch: pytest.MonkeyPatch) -> None:
    req = httpx2.Request("POST", "https://vibecode.moe/v1/messages")
    _install(monkeypatch, anthropic.APIConnectionError(request=req))
    with pytest.raises(GptApiError) as ei:
        await am.chat_messages(
            base_url="https://vibecode.moe/v1",
            api_key="vk",
            body={"model": "claude-opus-5", "messages": [{"role": "user", "content": "u"}]},
            timeout=5,
            use_model="claude-opus-5",
        )
    assert ei.value.context.get("error_kind") == "network"
    assert ei.value.retryable is True


# ── подключение в gpt_api ─────────────────────────────────────────────────


def _vibecode_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    import app.services.gpt_api as gpt_api
    import app.settings as settings_mod
    from app.settings import Settings

    monkeypatch.setenv("TEXT_LLM_PROVIDER", "vibecode")
    monkeypatch.setenv("VIBECODE_API_KEY", "vk-test")
    monkeypatch.setenv("VIBECODE_BASE_URL", "https://vibecode.moe/v1")
    monkeypatch.setenv("GPT_API_KEY", "")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    s = Settings()
    monkeypatch.setattr(settings_mod, "settings", s)
    monkeypatch.setattr(gpt_api, "settings", s)
    monkeypatch.setattr(am, "settings", s)
    return s


@pytest.mark.asyncio
async def test_vibecode_default_is_claude_via_messages(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Без choice.json на vibecode активен Claude Opus 5 — и уходит в /v1/messages, не в chat/completions."""
    import app.services.gpt_api as gpt_api

    s = _vibecode_settings(monkeypatch, tmp_path)
    assert s.gpt_model_effective == "claude-opus-5"
    assert "Claude Opus 5" in s.text_llm_label

    called: dict[str, Any] = {}

    async def fake_messages(**kw):
        called.update(kw)
        return GptChatResult(
            text="from-claude", model=kw["use_model"], finish_reason="stop", served_model="claude-opus-5"
        )

    async def boom_stream(**kw):
        raise AssertionError("claude must not go to chat/completions")

    monkeypatch.setattr(am, "chat_messages", fake_messages)
    monkeypatch.setattr(gpt_api, "_chat_completions_stream", boom_stream)

    r = await gpt_api.chat(prompt="о чём ролик?", timeout=5, max_retries=0, auto_pack=False)
    assert r.text == "from-claude"
    assert called["use_model"] == "claude-opus-5"
    assert called["base_url"] == "https://vibecode.moe/v1"
    assert called["api_key"] == "vk-test"
    assert called["body"]["messages"][-1]["content"] == "о чём ролик?"
    # vibecode.moe не в GPT_STRUCTURED_RELAYS → схема не прикрепляется
    assert called["structured"] is False


@pytest.mark.asyncio
async def test_vibecode_gpt_still_uses_completions_stream(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import app.services.gpt_api as gpt_api
    from app.services import text_llm_catalog as cat

    s = _vibecode_settings(monkeypatch, tmp_path)
    cat.write_choice(provider="vibecode", model_id="gpt-5.6-sol", cfg=s)
    assert s.gpt_model_effective == "gpt-5.6-sol"

    called: dict[str, Any] = {}

    async def fake_stream(**kw):
        called.update(kw)
        return GptChatResult(text="from-gpt", model=kw["use_model"], finish_reason="stop")

    async def boom_messages(**kw):
        raise AssertionError("gpt must not go to /v1/messages")

    monkeypatch.setattr(gpt_api, "_chat_completions_stream", fake_stream)
    monkeypatch.setattr(am, "chat_messages", boom_messages)

    r = await gpt_api.chat(prompt="p", timeout=5, max_retries=0, auto_pack=False)
    assert r.text == "from-gpt"
    assert "/v1/chat/completions" in called["url"]


@pytest.mark.asyncio
async def test_node_override_claude_routes_to_messages(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Шапка kie, на ноде выбран Claude из снапшота vibecode — /v1/messages с vibecode-ключом."""
    import app.services.gpt_api as gpt_api
    import app.settings as settings_mod
    from app.services.llm_override import NodeLlmOverride, use_override
    from app.settings import Settings

    monkeypatch.setenv("TEXT_LLM_PROVIDER", "kie")
    monkeypatch.setenv("GPT_API_KEY", "kie-key")
    monkeypatch.setenv("GPT_BASE_URL", "https://api.kie.ai")
    monkeypatch.setenv("VIBECODE_API_KEY", "vk-test")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    s = Settings()
    monkeypatch.setattr(settings_mod, "settings", s)
    monkeypatch.setattr(gpt_api, "settings", s)
    monkeypatch.setattr(am, "settings", s)

    called: dict[str, Any] = {}

    async def fake_messages(**kw):
        called.update(kw)
        return GptChatResult(
            text="ok", model=kw["use_model"], finish_reason="stop", served_model="claude-sonnet-5"
        )

    monkeypatch.setattr(am, "chat_messages", fake_messages)
    ov = NodeLlmOverride(
        model_id="claude-sonnet-5",
        channel="stable",
        kind="text",
        provider="vibecode",
        label="Claude Sonnet 5",
    )
    with use_override(ov):
        r = await gpt_api.chat(prompt="p", timeout=5, max_retries=0, auto_pack=False)
    assert r.text == "ok"
    assert called["use_model"] == "claude-sonnet-5"
    assert called["api_key"] == "vk-test"
    assert called["base_url"].startswith("https://vibecode.moe")


def test_catalog_has_claude_and_aliases(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import text_llm_catalog as cat

    ids = [it["id"] for it in cat.CATALOG]
    assert ids[:2] == ["claude-opus-5-vibecode", "claude-sonnet-5-vibecode"]
    assert cat.catalog_api_model("claude-opus-5") == "claude-opus-5"
    assert cat.catalog_api_model("sonnet") == "claude-sonnet-5"
    assert cat.catalog_api_model("gpt-5.6-sol") == "gpt-5.6-sol"
    assert cat.catalog_api_model(None) == "claude-opus-5"
    item = cat.catalog_item("claude-sonnet-5")
    assert item is not None and item["provider"] == "vibecode" and item["api_model"] == "claude-sonnet-5"


def test_minimax_text_provider_is_gone(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Старое TEXT_LLM_PROVIDER=minimax не роняет старт — активен kie-дефолт."""
    from app.settings import Settings

    monkeypatch.setenv("TEXT_LLM_PROVIDER", "minimax")
    monkeypatch.setenv("MINIMAX_API_KEY", "k")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    s = Settings()
    assert s.resolved_text_llm_provider() == "kie"
    assert not hasattr(s, "text_llm_is_minimax")


@pytest.mark.asyncio
async def test_contract_mode_truncated_claude_is_retryable_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Обрез по max_tokens в контрактном режиме — ошибка на ретрай целого вызова, не склейка."""
    import app.services.gpt_api as gpt_api

    _vibecode_settings(monkeypatch, tmp_path)

    async def fake_messages(**kw):
        return GptChatResult(
            text='{"say": "обор', model=kw["use_model"], finish_reason="length", served_model="claude-opus-5"
        )

    monkeypatch.setattr(am, "chat_messages", fake_messages)
    with pytest.raises(GptApiError) as ei:
        await gpt_api.chat(prompt="p", timeout=5, max_retries=0, auto_pack=False, response_schema=SCHEMA)
    assert ei.value.context.get("error_kind") == "truncated_contract"
    assert ei.value.retryable is True


def test_catalog_status_labels_active_claude(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from app.services import text_llm_catalog as cat

    s = _vibecode_settings(monkeypatch, tmp_path)
    st = cat.catalog_status(s)
    assert st["active_provider"] == "vibecode"
    assert st["active_model"] == "claude-opus-5"
    assert st["active_label"] == "Claude Opus 5 · vibecode.moe (claude-opus-5)"
    active = [m for m in st["models"] if m["active"]]
    assert len(active) == 1 and active[0]["id"] == "claude-opus-5-vibecode" and active[0]["key_configured"]


def test_content_block_fallbacks() -> None:
    assert am._content_block("plain") == {"type": "text", "text": "plain"}
    assert am._content_block(42) == {"type": "text", "text": "42"}
    # OpenAI-специфичный тип без text — не теряем молча
    assert am._content_block({"type": "input_file", "file_id": "f1"}) == {
        "type": "text",
        "text": str({"type": "input_file", "file_id": "f1"}),
    }
    assert am._content_block({"type": "input_file", "text": "t"}) == {"type": "text", "text": "t"}


def test_convert_messages_system_list_and_unknown_role() -> None:
    system, msgs = am.convert_messages(
        [
            {"role": "system", "content": [{"type": "text", "text": "A"}, "B"]},
            {"role": "tool", "content": "t"},
            {"role": "user", "content": "u"},
        ]
    )
    assert system == "A\nB"
    assert msgs == [{"role": "user", "content": "t"}, {"role": "user", "content": "u"}]


def test_client_targets_gateway_without_sdk_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(am.settings, "gpt_stream_read_timeout_s", 0.0, raising=False)
    c = am._client(base_url="https://vibecode.moe/v1", api_key="vk", timeout=5)
    assert str(c.base_url).rstrip("/") == "https://vibecode.moe"
    assert c.max_retries == 0
    assert c.api_key == "vk"
    assert c.timeout.read is None

    monkeypatch.setattr(am.settings, "gpt_stream_read_timeout_s", 30.0, raising=False)
    c2 = am._client(base_url="https://vibecode.moe", api_key="vk", timeout=5)
    assert c2.timeout.read == 120.0  # idle-предохранитель не ниже 120 с


@pytest.mark.asyncio
async def test_chat_messages_timeout_retryable(monkeypatch: pytest.MonkeyPatch) -> None:
    req = httpx2.Request("POST", "https://vibecode.moe/v1/messages")
    _install(monkeypatch, anthropic.APITimeoutError(request=req))
    with pytest.raises(GptApiError) as ei:
        await am.chat_messages(
            base_url="https://vibecode.moe/v1",
            api_key="vk",
            body={"model": "claude-opus-5", "messages": [{"role": "user", "content": "u"}]},
            timeout=5,
            use_model="claude-opus-5",
        )
    assert ei.value.context.get("error_kind") == "timeout"
    assert ei.value.retryable is True


# ── нативный tool-calling (агент студии) ────────────────────────────────────


def test_tool_blocks_pass_through_untouched() -> None:
    """tool_use / tool_result в истории уходят как есть: без id вызова API откажет."""
    use = {"type": "tool_use", "id": "toolu_1", "name": "showStages", "input": {"project_id": 1}}
    res = {"type": "tool_result", "tool_use_id": "toolu_1", "content": "{}", "is_error": True}
    _, out = am.convert_messages(
        [
            {"role": "user", "content": "где мы?"},
            {"role": "assistant", "content": [use]},
            {"role": "user", "content": [res, {"type": "text", "text": "продолжай"}]},
        ]
    )
    assert out[1]["content"] == [use]
    assert out[2]["content"][0] == res
    assert out[2]["content"][1] == {"type": "text", "text": "продолжай"}


def test_build_request_carries_tools() -> None:
    tools = [{"name": "showStages", "description": "где проект", "input_schema": {"type": "object"}}]
    body = {"model": "claude-opus-5", "messages": [{"role": "user", "content": "u"}], "tools": tools}
    req = am.build_request(body)
    assert req["tools"] == tools
    assert "tool_choice" not in req
    req = am.build_request({**body, "tool_choice": {"type": "auto"}})
    assert req["tool_choice"] == {"type": "auto"}
    assert "tools" not in am.build_request({"model": "m", "messages": []})


@pytest.mark.asyncio
async def test_chat_messages_tool_use_is_not_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ответ из одних tool_use — штатный, а не «пустой output»; блоки отдаются целиком."""
    msg = SimpleNamespace(
        id="msg_2",
        model="claude-opus-5",
        stop_reason="tool_use",
        stop_details=None,
        content=[
            SimpleNamespace(type="text", text="Смотрю."),
            SimpleNamespace(type="tool_use", id="toolu_9", name="showStages", input={"project_id": 1}),
        ],
        usage=SimpleNamespace(input_tokens=1, output_tokens=1),
    )
    _install(monkeypatch, msg)
    body = {"model": "claude-opus-5", "messages": [{"role": "user", "content": "u"}]}
    r = await am.chat_messages(
        base_url="https://vibecode.moe/v1", api_key="vk", body=body, timeout=5, use_model="claude-opus-5"
    )
    assert r.finish_reason == "tool_use"
    assert r.text == "Смотрю."
    assert r.raw["content"] == [
        {"type": "text", "text": "Смотрю."},
        {"type": "tool_use", "id": "toolu_9", "name": "showStages", "input": {"project_id": 1}},
    ]

    # Одни tool_use без текста — норма только когда инструменты были в запросе
    # (агент студии). Без них тот же ответ — пустой, см. тест ниже.
    only_tool = SimpleNamespace(**{**msg.__dict__, "content": [msg.content[1]]})
    _install(monkeypatch, only_tool)
    with_tools = {
        **body,
        "tools": [{"name": "showStages", "description": "d", "input_schema": {"type": "object"}}],
    }
    r = await am.chat_messages(
        base_url="https://vibecode.moe/v1",
        api_key="vk",
        body=with_tools,
        timeout=5,
        use_model="claude-opus-5",
    )
    assert r.text == "" and r.raw["content"][0]["type"] == "tool_use"


@pytest.mark.asyncio
async def test_chat_passes_tools_only_to_claude(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """`chat(tools=…)`: на Claude — в тело, история с блоками — целиком, пустой prompt не становится сообщением."""
    import app.services.gpt_api as gpt_api

    _vibecode_settings(monkeypatch, tmp_path)
    called: dict[str, Any] = {}

    async def fake_messages(**kw):
        called.update(kw)
        return GptChatResult(
            text="ok", model=kw["use_model"], finish_reason="stop", served_model="claude-opus-5"
        )

    monkeypatch.setattr(am, "chat_messages", fake_messages)
    tools = [{"name": "showStages", "description": "d", "input_schema": {"type": "object"}}]
    history = [
        {"role": "user", "content": "где мы?"},
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "t1", "name": "showStages", "input": {}}],
        },
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "{}"}]},
    ]
    assert gpt_api.native_tools_available() is True
    await gpt_api.chat(prompt="", history=history, tools=tools, timeout=5, max_retries=0, auto_pack=False)
    assert called["body"]["tools"] == tools
    assert called["body"]["messages"] == history


def test_block_to_dict_unknown_block_is_text_not_crash() -> None:
    from types import SimpleNamespace

    assert am._block_to_dict(SimpleNamespace(type="thinking", text="")) == {"type": "text", "text": ""}
    assert am._block_to_dict(SimpleNamespace(type="text", text="a")) == {"type": "text", "text": "a"}


@pytest.mark.asyncio
async def test_tools_ignored_off_claude_route(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Не-Claude маршрут: tools в тело не попадают, вызов идёт как обычно."""
    import app.services.gpt_api as gpt_api
    from app.services import text_llm_catalog as cat

    s = _vibecode_settings(monkeypatch, tmp_path)
    cat.write_choice(provider="vibecode", model_id="gpt-5.6-sol", cfg=s)
    assert gpt_api.native_tools_available() is False

    called: dict[str, Any] = {}

    async def fake_stream(**kw):
        called.update(kw)
        return GptChatResult(text='{"say": "ok"}', model=kw["use_model"], finish_reason="stop")

    monkeypatch.setattr(gpt_api, "_chat_completions_stream", fake_stream)
    r = await gpt_api.chat(
        prompt="x",
        tools=[{"name": "t", "description": "d", "input_schema": {"type": "object"}}],
        timeout=5,
        max_retries=0,
        auto_pack=False,
    )
    assert r.text == '{"say": "ok"}'
    assert "tools" not in called["body"]


@pytest.mark.asyncio
async def test_chat_messages_tool_use_without_tools_is_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """Релей отдал `stop_reason=tool_use` без текста на запрос без инструментов
    (прод 2026-08-27, сжатие промта CH02). Для текстового вызова это пустой
    ответ — ретраится, а не возвращается как ""."""
    msg = SimpleNamespace(
        id="msg_1",
        model="claude-opus-5",
        stop_reason="tool_use",
        stop_details=None,
        content=[SimpleNamespace(type="tool_use", id="tu_1", name="ghost", input={})],
        usage=SimpleNamespace(input_tokens=7, output_tokens=3),
    )
    _install(monkeypatch, msg)
    with pytest.raises(GptApiError) as ei:
        await am.chat_messages(
            base_url="https://vibecode.moe/v1",
            api_key="vk",
            body={"model": "claude-opus-5", "messages": [{"role": "user", "content": "u"}]},
            timeout=5,
            use_model="claude-opus-5",
        )
    assert ei.value.context.get("error_kind") == "empty_stream"
    assert ei.value.retryable is True


@pytest.mark.asyncio
async def test_chat_messages_tool_use_with_tools_is_fine(monkeypatch: pytest.MonkeyPatch) -> None:
    msg = SimpleNamespace(
        id="msg_1",
        model="claude-opus-5",
        stop_reason="tool_use",
        stop_details=None,
        content=[SimpleNamespace(type="tool_use", id="tu_1", name="edit", input={"a": 1})],
        usage=SimpleNamespace(input_tokens=7, output_tokens=3),
    )
    _install(monkeypatch, msg)
    r = await am.chat_messages(
        base_url="https://vibecode.moe/v1",
        api_key="vk",
        body={
            "model": "claude-opus-5",
            "messages": [{"role": "user", "content": "u"}],
            "tools": [{"name": "edit", "description": "d", "input_schema": {"type": "object"}}],
        },
        timeout=5,
        use_model="claude-opus-5",
    )
    assert r.finish_reason == "tool_use"
    assert r.raw["content"][0]["name"] == "edit"
