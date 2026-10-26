"""Единая точка ключей: provider_keys.resolve."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.services.provider_keys import (
    ProviderKeyError,
    configured,
    resolve,
)
from app.settings import Settings


def _settings(monkeypatch, tmp_path: Path, **env: str) -> Settings:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    for k, v in env.items():
        if v is None:
            monkeypatch.delenv(k, raising=False)
        else:
            monkeypatch.setenv(k, v)
    return Settings()


def test_kie_market_uses_kie_key_not_vibecode(monkeypatch, tmp_path: Path) -> None:
    s = _settings(
        monkeypatch,
        tmp_path,
        KIE_API_KEY="kie-only",
        GPT_API_KEY="gpt-fallback",
        VIBECODE_API_KEY="vk-must-not-leak",
        KIE_API_BASE_URL="https://api.kie.ai",
        VIBECODE_BASE_URL="https://vibecode.moe/v1",
    )
    creds = resolve("kie", "market", cfg=s)
    assert creds["api_key"] == "kie-only"
    assert "kie.ai" in creds["base_url"]
    assert creds["headers"]["Authorization"] == "Bearer kie-only"
    assert "vk-must-not-leak" not in str(creds)


def test_kie_falls_back_to_gpt_not_vibecode(monkeypatch, tmp_path: Path) -> None:
    s = _settings(
        monkeypatch,
        tmp_path,
        KIE_API_KEY="",
        GPT_API_KEY="gpt-as-kie",
        VIBECODE_API_KEY="vk-no",
        GPT_BASE_URL="https://api.kie.ai",
    )
    creds = resolve("kie", "market", cfg=s)
    assert creds["api_key"] == "gpt-as-kie"
    assert creds["api_key"] != "vk-no"


def test_vibecode_never_gets_kie_key(monkeypatch, tmp_path: Path) -> None:
    s = _settings(
        monkeypatch,
        tmp_path,
        KIE_API_KEY="kie-key",
        GPT_API_KEY="gpt-key",
        VIBECODE_API_KEY="",
        VIBECODE_BASE_URL="https://vibecode.moe/v1",
    )
    creds = resolve("vibecode", "image", cfg=s)
    assert creds["api_key"] == ""
    assert "kie-key" not in str(creds)
    assert "gpt-key" not in str(creds)
    with pytest.raises(ProviderKeyError, match="VIBECODE_API_KEY"):
        resolve("vibecode", "image", cfg=s, require_key=True)


def test_vibecode_image_key_and_url(monkeypatch, tmp_path: Path) -> None:
    s = _settings(
        monkeypatch,
        tmp_path,
        VIBECODE_API_KEY="vk-test",
        VIBECODE_BASE_URL="https://vibecode.moe/v1",
        GPT_API_KEY="kie-key",
    )
    creds = resolve("vibecode", "image", cfg=s)
    assert creds["api_key"] == "vk-test"
    assert creds["base_url"] == "https://vibecode.moe/v1"
    assert creds["headers"]["Authorization"] == "Bearer vk-test"


def test_gpt_text_relay_adds_header(monkeypatch, tmp_path: Path) -> None:
    s = _settings(
        monkeypatch,
        tmp_path,
        TEXT_LLM_PROVIDER="kie",
        GPT_API_KEY="kie-key",
        GPT_BASE_URL="https://gpt.example.com",
        GPT_RELAY_TOKEN="relay-secret",
        VIBECODE_API_KEY="vk-test",
    )
    creds = resolve("gpt", "text", cfg=s)
    assert creds["api_key"] == "kie-key"
    assert creds["base_url"] == "https://gpt.example.com"
    assert creds["headers"]["X-VP-Relay-Token"] == "relay-secret"


def test_vibecode_text_stays_direct_when_relay_set(monkeypatch, tmp_path: Path) -> None:
    """Активный vibecode не уходит на VPS — иначе 401 envelope."""
    from app.services import text_llm_catalog as cat

    s = _settings(
        monkeypatch,
        tmp_path,
        TEXT_LLM_PROVIDER="vibecode",
        VIBECODE_API_KEY="vk-test",
        VIBECODE_BASE_URL="https://vibecode.moe/v1",
        GPT_BASE_URL="https://gpt.example.com",
        GPT_RELAY_TOKEN="relay-secret",
        GPT_API_KEY="kie-key",
    )
    cat.write_choice(provider="vibecode", model_id="gpt-5.5-vibecode", cfg=s)
    creds = resolve("gpt", "text", cfg=s)
    assert creds["api_key"] == "vk-test"
    assert creds["base_url"] == "https://vibecode.moe/v1"
    assert "X-VP-Relay-Token" not in creds["headers"]


def test_vibecode_purpose_relay_uses_vps(monkeypatch, tmp_path: Path) -> None:
    s = _settings(
        monkeypatch,
        tmp_path,
        VIBECODE_API_KEY="vk-test",
        VIBECODE_BASE_URL="https://vibecode.moe/v1",
        GPT_BASE_URL="https://gpt.example.com",
        GPT_RELAY_TOKEN="relay-secret",
    )
    creds = resolve("vibecode", "relay", cfg=s)
    assert creds["api_key"] == "vk-test"
    assert creds["base_url"] == "https://gpt.example.com"
    assert creds["headers"]["X-VP-Relay-Token"] == "relay-secret"


def test_outsee_wavespeed_eleven_minimax(monkeypatch, tmp_path: Path) -> None:
    s = _settings(
        monkeypatch,
        tmp_path,
        OUTSEE_API_KEY="os-key",
        OUTSEE_API_BASE_URL="https://outsee.io",
        WAVESPEED_API_KEY="ws-key",
        ELEVENLABS_API_KEY="el-key",
        MINIMAX_API_KEY="mm-key",
        MINIMAX_BASE_URL="https://api.minimax.io",
    )
    assert resolve("outsee", cfg=s)["api_key"] == "os-key"
    assert resolve("wavespeed", cfg=s)["api_key"] == "ws-key"
    el = resolve("elevenlabs", cfg=s)
    assert el["api_key"] == "el-key"
    assert el["headers"]["xi-api-key"] == "el-key"
    assert "Authorization" not in el["headers"]
    assert resolve("minimax", cfg=s)["api_key"] == "mm-key"
    assert configured("outsee", cfg=s) is True
    assert configured("kie", cfg=s) is False


def test_unknown_provider_raises(monkeypatch, tmp_path: Path) -> None:
    s = _settings(monkeypatch, tmp_path)
    with pytest.raises(ProviderKeyError, match="неизвестный"):
        resolve("grsai", cfg=s)


def test_require_key_clear_error(monkeypatch, tmp_path: Path) -> None:
    s = _settings(monkeypatch, tmp_path, OUTSEE_API_KEY="")
    with pytest.raises(ProviderKeyError, match="OUTSEE_API_KEY"):
        resolve("outsee", cfg=s, require_key=True)
