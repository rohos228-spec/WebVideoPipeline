"""kie лежит: тот же запрос уходит в 52pay той же моделью; ошибки содержания и бюджета не переключают."""

from __future__ import annotations

import pytest

from app.services import gpt_api
from app.services.gpt_api import GptApiError, provider_fallback
from app.services.llm_override import NodeLlmOverride, use_override
from app.services.provider_breaker import ProviderCircuitOpen
from app.settings import settings


@pytest.fixture
def kie(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "text_llm_fallback_52pay", True)
    monkeypatch.setattr(settings, "text_llm_provider", "kie")
    monkeypatch.setattr(type(settings), "text_llm_is_vibecode", property(lambda self: False))
    monkeypatch.setattr(type(settings), "text_llm_is_pay52", property(lambda self: False))
    monkeypatch.setattr(settings, "pay52_gpt_key", "test-key-not-real")


def _infra(code: int = 502) -> GptApiError:
    return GptApiError(f"GPT HTTP {code}", context={"retryable": True, "status_code": code})


def test_infra_failure_switches_to_52pay(kie: None) -> None:
    ov = provider_fallback(_infra(), {"model": "gpt-5.6-sol"})
    assert ov is not None and ov.provider == "52pay" and ov.model_id == "gpt-5.6-sol"
    assert provider_fallback(ProviderCircuitOpen("kie:responses", 30.0), {"model": "gpt-5.6-sol"}) is not None


def test_content_and_budget_errors_do_not_switch(kie: None) -> None:
    bad = GptApiError("GPT: схема", context={"retryable": False})
    assert provider_fallback(bad, {"model": "gpt-5.6-sol"}) is None
    assert provider_fallback(RuntimeError("x"), {"model": "gpt-5.6-sol"}) is None


def test_no_switch_without_route_files_or_with_own_provider(kie: None, monkeypatch) -> None:
    assert provider_fallback(_infra(), {"model": "some-unknown-model"}) is None
    assert provider_fallback(_infra(), {"model": "gpt-5.6-sol", "input_paths": ["a.pdf"]}) is None
    with use_override(
        NodeLlmOverride(model_id="m", channel="stable", kind="text", provider="vibecode", label="v")
    ):
        assert provider_fallback(_infra(), {"model": "gpt-5.6-sol"}) is None
    monkeypatch.setattr(settings, "pay52_gpt_key", "")
    assert provider_fallback(_infra(), {"model": "gpt-5.6-sol"}) is None
    monkeypatch.setattr(settings, "text_llm_fallback_52pay", False)
    assert provider_fallback(_infra(), {"model": "gpt-5.6-sol"}) is None


@pytest.mark.asyncio
async def test_chat_retries_once_through_52pay(kie: None, monkeypatch) -> None:
    from app.services.llm_override import current_override

    seen: list[str | None] = []

    async def fake_unscoped(**kwargs):
        ov = current_override()
        seen.append(ov.provider if ov else None)
        if ov is None:
            raise _infra(503)
        return gpt_api.GptChatResult(text="ok", model="gpt-5.6-sol")

    async def no_budget(kwargs=None):
        return None, None

    monkeypatch.setattr(gpt_api, "_chat_unscoped", fake_unscoped)
    monkeypatch.setattr(gpt_api, "_check_budget_before_call", no_budget)
    out = await gpt_api.chat(prompt="привет", model="gpt-5.6-sol")
    assert out.text == "ok"
    assert seen == [None, "52pay"]
