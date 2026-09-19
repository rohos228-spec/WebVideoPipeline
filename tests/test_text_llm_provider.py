"""TEXT_LLM: GPT (kie) по умолчанию + vibecode по явному выбору."""

from __future__ import annotations

from pathlib import Path

from app.settings import Settings


def test_default_is_kie_gpt_not_tokenrouter(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TEXT_LLM_PROVIDER", "kie")
    monkeypatch.setenv("TOKENROUTER_API_KEY", "tr-present-but-ignored")
    monkeypatch.setenv("GPT_API_KEY", "kie-key")
    monkeypatch.setenv("GPT_BASE_URL", "https://api.kie.ai")
    monkeypatch.setenv("GPT_MODEL", "gpt-5-6-sol")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    s = Settings()
    assert s.resolved_text_llm_provider() == "kie"
    assert s.gpt_api_effective_key == "kie-key"
    assert "GPT" in s.text_llm_label
    assert "Kimi" not in s.text_llm_label


def test_retired_tokenrouter_falls_back_to_kie(monkeypatch, tmp_path: Path) -> None:
    """TokenRouter выведен: старое значение в .env не роняет старт, а уводит на kie."""
    monkeypatch.setenv("TEXT_LLM_PROVIDER", "tokenrouter")
    monkeypatch.setenv("GPT_API_KEY", "kie-key")
    monkeypatch.setenv("GPT_BASE_URL", "https://api.kie.ai")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    s = Settings()
    assert s.resolved_text_llm_provider() == "kie"
    assert s.gpt_api_effective_key == "kie-key"


def test_choice_file_switches_without_touching_gpt_env(monkeypatch, tmp_path: Path) -> None:
    from app.services import text_llm_catalog as cat

    monkeypatch.setenv("TEXT_LLM_PROVIDER", "kie")
    monkeypatch.setenv("TOKENROUTER_API_KEY", "tr-key")
    monkeypatch.setenv("GPT_API_KEY", "kie-key")
    monkeypatch.setenv("GPT_BASE_URL", "https://api.kie.ai")
    monkeypatch.setenv("GPT_MODEL", "gpt-5-6-sol")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    s = Settings()

    assert s.resolved_text_llm_provider() == "kie"
    cat.write_choice(provider="vibecode", model_id="claude-opus-5", cfg=s)
    assert s.resolved_text_llm_provider() == "vibecode"
    cat.write_choice(provider="kie", model_id="gpt-kie", cfg=s)
    assert s.resolved_text_llm_provider() == "kie"
    assert s.gpt_model == "gpt-5-6-sol"  # GPT_* не трогали


def test_vibecode_models_switch_url_and_key(monkeypatch, tmp_path: Path) -> None:
    import app.services.gpt_api as gpt_api
    import app.settings as settings_mod
    from app.services import text_llm_catalog as cat

    monkeypatch.setenv("TEXT_LLM_PROVIDER", "kie")
    monkeypatch.setenv("GPT_API_KEY", "kie-key")
    monkeypatch.setenv("GPT_BASE_URL", "https://api.kie.ai")
    monkeypatch.setenv("GPT_CHAT_PATH", "/codex/v1/responses")
    monkeypatch.setenv("VIBECODE_API_KEY", "vk-test")
    monkeypatch.setenv("VIBECODE_BASE_URL", "https://vibecode.moe/v1")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    s = Settings()
    monkeypatch.setattr(settings_mod, "settings", s)
    monkeypatch.setattr(gpt_api, "settings", s)

    cat.write_choice(provider="vibecode", model_id="gpt-5.5", cfg=s)
    assert s.resolved_text_llm_provider() == "vibecode"
    assert s.gpt_model_effective == "gpt-5.5"
    assert s.gpt_api_effective_key == "vk-test"
    assert s.gpt_chat_path_effective == "/chat/completions"
    assert gpt_api.is_responses_mode() is False
    assert gpt_api._chat_url(s.gpt_model_effective) == ("https://vibecode.moe/v1/chat/completions")

    cat.write_choice(provider="vibecode", model_id="gpt-5.6-sol", cfg=s)
    assert s.gpt_model_effective == "gpt-5.6-sol"
    assert "GPT 5.6 Sol" in s.text_llm_label

    cat.write_choice(provider="kie", model_id="gpt-kie", cfg=s)
    assert s.resolved_text_llm_provider() == "kie"
    assert s.gpt_api_effective_key == "kie-key"
    assert s.gpt_api_effective_base_url == "https://api.kie.ai"


def test_vibecode_stays_direct_even_when_vps_relay_set(monkeypatch, tmp_path: Path) -> None:
    """VPS relay is for kie; vibecode must hit vibecode.moe (stale relay = 401 envelope)."""
    import app.services.gpt_api as gpt_api
    import app.settings as settings_mod
    from app.services import text_llm_catalog as cat

    monkeypatch.setenv("TEXT_LLM_PROVIDER", "vibecode")
    monkeypatch.setenv("VIBECODE_API_KEY", "vk-test")
    monkeypatch.setenv("VIBECODE_BASE_URL", "https://vibecode.moe/v1")
    monkeypatch.setenv("GPT_BASE_URL", "https://gpt.example.com")
    monkeypatch.setenv("GPT_RELAY_TOKEN", "relay-secret")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    s = Settings()
    monkeypatch.setattr(settings_mod, "settings", s)
    monkeypatch.setattr(gpt_api, "settings", s)
    cat.write_choice(provider="vibecode", model_id="gpt-5.5-vibecode", cfg=s)
    assert s.gpt_api_effective_base_url == "https://vibecode.moe/v1"
    assert gpt_api._chat_url("gpt-5.5") == "https://vibecode.moe/v1/chat/completions"
    assert gpt_api._headers()["Authorization"] == "Bearer vk-test"
    cat.write_choice(provider="kie", model_id="gpt-kie", cfg=s)
    assert s.gpt_api_effective_base_url == "https://gpt.example.com"
    assert gpt_api._chat_url("gpt-5-6-sol") == ("https://gpt.example.com/codex/v1/responses")


def test_parse_chat_completions_sse() -> None:
    from app.services.gpt_api import parse_chat_completions_sse_lines

    lines = [
        'data: {"choices":[{"delta":{"content":"hel"}}]}',
        'data: {"choices":[{"delta":{"content":"lo"},"finish_reason":"stop"}]}',
        "data: [DONE]",
    ]
    text, finish, _ = parse_chat_completions_sse_lines(lines)
    assert text == "hello"
    assert finish == "stop"


def test_parse_chat_sse_salvages_text_past_envelope_error() -> None:
    """ddos-guard: событие-ошибка среди чанков не убивает текст (relay-fix)."""
    from app.services.gpt_api import parse_chat_completions_sse_lines

    lines = [
        'data: {"code":502,"msg":"upstream_error"}',
        'data: {"choices":[{"delta":{"content":"hi"}}]}',
        'data: {"choices":[{"delta":{"content":"!"},"finish_reason":"stop"}]}',
        "data: [DONE]",
    ]
    text, finish, _ = parse_chat_completions_sse_lines(lines)
    assert text == "hi!"
    assert finish == "stop"


def test_parse_chat_completions_json_without_data_prefix() -> None:
    from app.services.gpt_api import parse_chat_completions_sse_lines

    lines = [
        '{"choices":[{"message":{"content":"полный промт кадра"},"finish_reason":"stop"}]}',
    ]
    text, finish, _ = parse_chat_completions_sse_lines(lines)
    assert text == "полный промт кадра"
    assert finish == "stop"


def test_parse_chat_completions_message_when_delta_has_only_role() -> None:
    from app.services.gpt_api import parse_chat_completions_sse_lines

    lines = [
        'data: {"choices":[{"delta":{"role":"assistant"},"message":{"content":"сцена"}}]}',
    ]
    text, _, _ = parse_chat_completions_sse_lines(lines)
    assert text == "сцена"


def test_check_provider_envelope_openai_error() -> None:
    import pytest

    from app.services.gpt_api import GptApiError, _check_provider_envelope

    with pytest.raises(GptApiError, match="overloaded") as ei:
        _check_provider_envelope({"error": {"message": "The server is overloaded"}})
    assert ei.value.retryable is True


def test_parse_chat_sse_only_envelope_error_raises() -> None:
    """Только ошибка без текста — кидаем её же, а не пустой output."""
    import pytest

    from app.services.gpt_api import GptApiError, parse_chat_completions_sse_lines

    lines = ['data: {"code":502,"msg":"upstream_error"}', "data: [DONE]"]
    with pytest.raises(GptApiError, match="502"):
        parse_chat_completions_sse_lines(lines)


def test_catalog_groups_and_snapshot_models() -> None:
    """Каждая запись каталога несёт group, а её api_model есть в снимке vibecode."""
    from app.services.text_llm_catalog import CATALOG, catalog_item
    from app.services.vibecode_catalog import load_snapshot

    snapshot = {m["id"] for m in load_snapshot()}
    groups = {it["group"] for it in CATALOG}
    assert groups == {"Anthropic", "OpenAI", "Google", "xAI", "KIE"}
    for it in CATALOG:
        api_model = it.get("api_model")
        if api_model:
            assert api_model in snapshot, f"{it['id']} → {api_model} нет в снимке vibecode"

    # tokenrouter/Kimi выведены целиком
    assert not any("tokenrouter" in it["id"] or it["provider"] == "tokenrouter" for it in CATALOG)
    assert catalog_item("kimi-k3-tokenrouter") is None

    # Модели, добавленные переносом из форка
    for mid in (
        "gpt-5.6-terra",
        "gpt-5.6-luna",
        "gemini-3.1-pro-preview",
        "gemini-3.8-flash",
        "claude-opus-4-8",
        "claude-fable-5",
        "grok-4-6",
    ):
        item = catalog_item(mid)
        assert item is not None, mid
        assert item["api_model"] == mid

    # Дефолт vibecode не съехал, алиас .env-написания жив
    assert catalog_item("gpt-5-6-sol")["id"] == "gpt-5.6-sol-vibecode"
    assert catalog_item("claude-opus-5")["id"] == "claude-opus-5-vibecode"


def test_catalog_status_exposes_group(monkeypatch, tmp_path: Path) -> None:
    from app.services.text_llm_catalog import catalog_status

    monkeypatch.setenv("TEXT_LLM_PROVIDER", "kie")
    monkeypatch.setenv("GPT_API_KEY", "kie-key")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    st = catalog_status(Settings())
    assert st["models"]
    assert all(m.get("group") for m in st["models"])


def test_claude_models_route_to_messages() -> None:
    """Claude-записи каталога должны уходить на /v1/messages, остальные — нет."""
    from app.services.anthropic_messages import is_anthropic_model
    from app.services.text_llm_catalog import CATALOG

    for it in CATALOG:
        api_model = it.get("api_model") or ""
        assert is_anthropic_model(api_model) == (it["group"] == "Anthropic")


def test_vibecode_catalog_text_aliases() -> None:
    from app.services.vibecode_catalog import find_model

    assert find_model("gpt-5-6-sol")["id"] == "gpt-5.6-sol"
    assert find_model("gemini-3.1-pro")["id"] == "gemini-3.1-pro-preview"
    assert find_model("gemini-3.8-flash")["id"] == "gemini-3.8-flash"
    # Fable 5.1 в снимке нет — конфиг форка приземляется на Fable 5
    assert find_model("claude-fable-5-1")["id"] == "claude-fable-5"
