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

    cat.write_choice(provider="vibecode", model_id="gpt-6-sol", cfg=s)
    assert s.resolved_text_llm_provider() == "vibecode"
    assert s.gpt_model_effective == "gpt-6-sol"
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
    assert groups == {"Anthropic", "OpenAI", "Google", "xAI", "GLM", "KIE"}
    for it in CATALOG:
        api_model = it.get("api_model")
        if api_model and it.get("provider") == "vibecode":
            assert api_model in snapshot, f"{it['id']} → {api_model} нет в снимке vibecode"

    # tokenrouter/Kimi выведены целиком
    assert not any("tokenrouter" in it["id"] or it["provider"] == "tokenrouter" for it in CATALOG)
    assert catalog_item("kimi-k3-tokenrouter") is None

    # Модели, добавленные переносом из форка
    for mid in (
        "gpt-6-sol",
        "gpt-6-astra",
        "gpt-6-luna",
        "gemini-3.8-flash",
        "claude-sonnet-5-5",
        "claude-opus-5-5",
        "claude-fable-5",
        "grok-4-7",
        "glm-5.3-flash",
    ):
        item = catalog_item(mid)
        assert item is not None, mid
        assert item["api_model"] == mid

    # Дефолт vibecode не съехал, алиас .env-написания жив
    assert catalog_item("gpt-5-6-sol")["id"] == "gpt-5.6-sol-vibecode"
    assert catalog_item("claude-opus-5-5")["id"] == "claude-opus-5-5-vibecode"


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
    assert find_model("gemini-3.8-flash")["id"] == "gemini-3.8-flash"
    assert find_model("claude-fable-5-1")["id"] == "claude-fable-5-1"


def test_reasoning_effort_normalization_and_choice(monkeypatch, tmp_path: Path) -> None:
    from app.services import text_llm_catalog as cat

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("GPT_API_KEY", "kie-key")
    s = Settings()

    # По умолчанию — off
    assert cat.resolve_active_reasoning_effort(s) == "off"

    # Нормализация алиасов
    assert cat.normalize_reasoning_effort("низкий") == "low"
    assert cat.normalize_reasoning_effort("середина") == "medium"
    assert cat.normalize_reasoning_effort("высокий") == "high"
    assert cat.normalize_reasoning_effort("экстра") == "xhigh"
    assert cat.normalize_reasoning_effort("unknown_val") == "off"

    # Запись reasoning_effort через write_choice
    res = cat.write_choice(reasoning_effort="high", cfg=s)
    assert res["reasoning_effort"] == "high"
    assert cat.resolve_active_reasoning_effort(s) == "high"

    # catalog_status возвращает active_reasoning_effort и reasoning_options
    st = cat.catalog_status(s)
    assert st["active_reasoning_effort"] == "high"
    assert any(opt["id"] == "xhigh" for opt in st["reasoning_options"])
    assert any(opt["id"] == "off" for opt in st["reasoning_options"])


def test_anthropic_and_gpt_reasoning_wiring(monkeypatch, tmp_path: Path) -> None:
    import app.services.gpt_api as gpt_api
    import app.settings as settings_mod
    from app.services import text_llm_catalog as cat
    from app.services.anthropic_messages import build_request

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("GPT_API_KEY", "kie-key")
    s = Settings()
    monkeypatch.setattr(settings_mod, "settings", s)
    monkeypatch.setattr(cat, "settings", s)
    monkeypatch.setattr(gpt_api, "settings", s)

    # 1. Когда off — gpt_api reasoning block is None, anthropic thinking отсутствует
    cat.write_choice(reasoning_effort="off", cfg=s)
    assert gpt_api._responses_reasoning_block() is None

    req_off = build_request({"model": "claude-opus-5", "messages": [{"role": "user", "content": "hi"}]})
    assert "thinking" not in req_off

    # 2. Когда high — gpt_api reasoning block {"effort": "high"}, anthropic thinking enabled
    cat.write_choice(reasoning_effort="high", cfg=s)
    block = gpt_api._responses_reasoning_block()
    assert block == {"effort": "high"}

    req_high = build_request({"model": "claude-opus-5", "messages": [{"role": "user", "content": "hi"}]})
    assert req_high.get("thinking") == {"type": "enabled", "budget_tokens": 16384}
    assert req_high.get("max_tokens", 0) > 16384


def test_pay52_text_catalog_and_routing(monkeypatch, tmp_path: Path) -> None:
    """Проверяет каталог моделей 52pay и динамическое переключение ключей (GLM, Claude Low/High, GPT)."""
    import app.settings as settings_mod
    from app.services import text_llm_catalog as cat

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("PAY52_BASE_URL", "https://api.52pay.com/v1")
    monkeypatch.setenv("PAY52_GLM_KEY", "glm-key-test")
    monkeypatch.setenv("PAY52_CLAUDE_LOW_KEY", "claude-low-key-test")
    monkeypatch.setenv("PAY52_CLAUDE_HIGH_KEY", "claude-high-key-test")
    monkeypatch.setenv("PAY52_GPT_KEY", "gpt-key-test")

    s = Settings()
    monkeypatch.setattr(settings_mod, "settings", s)
    monkeypatch.setattr(cat, "settings", s)

    # Модели присутствуют в каталоге
    glm_item = cat.catalog_item("glm-5.3-pay52")
    assert glm_item is not None
    assert glm_item["provider"] == "pay52"
    assert glm_item["group"] == "GLM"
    assert glm_item["api_model"] == "glm-5.3"

    claude_low = cat.catalog_item("claude-sonnet-5-pay52")
    assert claude_low is not None
    assert claude_low["group"] == "Anthropic"

    claude_high = cat.catalog_item("claude-opus-5-5-pay52")
    assert claude_high is not None
    assert claude_high["group"] == "Anthropic"

    gpt_item = cat.catalog_item("gpt-6-sol-pay52")
    assert gpt_item is not None
    assert gpt_item["group"] == "OpenAI"

    # 1. Выбор GLM
    cat.write_choice(provider="pay52", model_id="glm-5.3-pay52", cfg=s)
    assert s.text_llm_is_pay52 is True
    assert s.gpt_api_effective_base_url == "https://api.52pay.com/v1"
    assert s.gpt_api_effective_key == "glm-key-test"
    assert s.gpt_chat_path_effective == "/chat/completions"

    # 2. Выбор Claude Low
    cat.write_choice(provider="pay52", model_id="claude-sonnet-5-pay52", cfg=s)
    assert s.gpt_api_effective_key == "claude-low-key-test"

    # 3. Выбор Claude High
    cat.write_choice(provider="pay52", model_id="claude-opus-5-5-pay52", cfg=s)
    assert s.gpt_api_effective_key == "claude-high-key-test"

    # 4. Выбор GPT
    cat.write_choice(provider="pay52", model_id="gpt-6-sol-pay52", cfg=s)
    assert s.gpt_api_effective_key == "gpt-key-test"


