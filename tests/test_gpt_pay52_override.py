"""Шаг просит 52pay (агент стиля): запрос уходит в 52pay, а не в общий провайдер kie."""

from __future__ import annotations

import pytest

from app.services import gpt_api
from app.services.llm_override import NodeLlmOverride, use_override
from app.settings import settings

OV = NodeLlmOverride(model_id="gpt-5.6-sol", channel="stable", kind="text", provider="52pay", label="52pay")


def test_pay52_override_routes_to_52pay_chat(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "pay52_gpt_key", "test-key-not-real")
    monkeypatch.setattr(settings, "pay52_base_url", "https://api.52pay.com/v1")
    with use_override(OV):
        route = gpt_api._pay52_override()
        assert route is not None
        assert route.url == "https://api.52pay.com/v1/chat/completions"
        assert route.api_model == "gpt-5.6-sol"
        assert route.headers()["Authorization"] == "Bearer test-key-not-real"
        assert gpt_api.is_responses_mode() is False


def test_without_key_or_override_stays_on_common_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "pay52_gpt_key", "")
    with use_override(OV):
        assert gpt_api._pay52_override() is None
    monkeypatch.setattr(settings, "pay52_gpt_key", "test-key-not-real")
    assert gpt_api._pay52_override() is None
    vibe = NodeLlmOverride(
        model_id="gpt-5.6-sol", channel="stable", kind="text", provider="vibecode", label="v"
    )
    with use_override(vibe):
        assert gpt_api._pay52_override() is None
