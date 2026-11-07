from __future__ import annotations

import asyncio

from app.orchestrator.routing.ledger import SpendLedger
from app.orchestrator.routing.model_router import DEAD_PROVIDER_AFTER, ModelRouter
from app.orchestrator.routing.transport import CompletionResult, ProviderError


class _Transport:
    def __init__(self, dead: set[str], error: str = "http 402") -> None:
        self.dead, self.error, self.calls = dead, error, []

    async def complete(self, request):
        self.calls.append(request.provider)
        if request.provider in self.dead:
            raise ProviderError(self.error, retryable=False, status_code=402)
        return CompletionResult(text="ok", finish_reason="stop", model=request.model)


async def _noop(_s):
    return None


def _router(transport):
    return ModelRouter(transport=transport, ledger=SpendLedger(), sleeper=_noop)


def _ask(router):
    return asyncio.run(router.call("strong", [{"role": "user", "content": "привет"}], run_id="t"))


def test_dead_provider_skipped_after_repeated_dead_errors():
    router = _router(_Transport(set()))
    first = router.config.strong_unconfirmed[0].provider
    transport = _Transport({first})
    router.transport = transport
    for _ in range(DEAD_PROVIDER_AFTER):
        assert _ask(router).text == "ok"
    before = transport.calls.count(first)
    assert before >= DEAD_PROVIDER_AFTER
    transport.calls.clear()
    assert _ask(router).text == "ok"
    assert first not in transport.calls


def test_success_resets_and_ordinary_errors_do_not_count():
    router = _router(_Transport(set()))
    router._note_provider("kie", "http 500 boom")
    router._note_provider("kie", "http 500 boom")
    router._note_provider("kie", "http 500 boom")
    assert not router._dead_open("kie")
    for _ in range(DEAD_PROVIDER_AFTER):
        router._note_provider("kie", "empty responses stream")
    assert router._dead_open("kie")
    router._note_provider("kie", None)
    assert not router._dead_open("kie")


def test_all_dead_still_tries_chain():
    router = _router(_Transport(set()))
    for hop in router.config.strong_unconfirmed:
        for _ in range(DEAD_PROVIDER_AFTER):
            router._note_provider(hop.provider, "http 402")
    transport = _Transport(set())
    router.transport = transport
    assert _ask(router).text == "ok"
    assert transport.calls
