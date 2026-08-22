"""Circuit breaker + Retry-After per-провайдер (п.16-17).

Что чинится: ретраи были слепыми. 429 лечился той же экспонентой, что
упавший шлюз; заголовок `Retry-After` не читался вообще; лежащий провайдер
выгребал полный набор попыток на каждом кадре — десятки минут в пустоту и
оплаченные неуспешные вызовы.
"""

from __future__ import annotations

import pytest

from app.services import provider_breaker as pb
from app.settings import settings


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch) -> None:
    pb.reset()
    monkeypatch.setattr(settings, "provider_breaker_enabled", True)
    monkeypatch.setattr(settings, "provider_breaker_failures", 3)
    monkeypatch.setattr(settings, "provider_breaker_cooldown_s", 60.0)
    monkeypatch.setattr(settings, "provider_min_interval_ms", 0)


# ── что считается «провайдер лежит» ──────────────────────────────────────


def test_429_is_not_an_infrastructure_failure() -> None:
    """Лимит — «приходи позже», а не «шлюз упал»: цепь на нём не рвём."""
    assert not pb.is_infrastructure_failure(429)
    assert pb.is_infrastructure_failure(500)
    assert pb.is_infrastructure_failure(503)
    assert pb.is_infrastructure_failure(None, "network")
    assert pb.is_infrastructure_failure(None, "timeout")
    assert not pb.is_infrastructure_failure(400)


def test_many_429_never_open_the_circuit() -> None:
    for _ in range(20):
        pb.note_failure("kie", status=429)
    assert pb.snapshot()["kie"]["state"] == pb.CLOSED


# ── размыкание / восстановление ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_opens_after_threshold_and_blocks_calls() -> None:
    for _ in range(3):
        pb.note_failure("kie", status=500)

    assert pb.snapshot()["kie"]["state"] == pb.OPEN
    with pytest.raises(pb.ProviderCircuitOpen):
        await pb.acquire("kie")


@pytest.mark.asyncio
async def test_below_threshold_stays_closed() -> None:
    pb.note_failure("kie", status=500)
    pb.note_failure("kie", status=500)
    await pb.acquire("kie")  # не должно бросить
    assert pb.snapshot()["kie"]["state"] == pb.CLOSED


def test_success_resets_failure_streak() -> None:
    pb.note_failure("kie", status=500)
    pb.note_failure("kie", status=500)
    pb.note_success("kie")
    pb.note_failure("kie", status=500)
    assert pb.snapshot()["kie"]["state"] == pb.CLOSED


@pytest.mark.asyncio
async def test_half_open_probe_then_close(monkeypatch: pytest.MonkeyPatch) -> None:
    """После cooldown пропускается один пробный вызов; успех закрывает цепь."""
    monkeypatch.setattr(settings, "provider_breaker_cooldown_s", 0.0)
    for _ in range(3):
        pb.note_failure("kie", status=500)

    await pb.acquire("kie")  # cooldown вышел → half_open
    assert pb.snapshot()["kie"]["state"] == pb.HALF_OPEN

    pb.note_success("kie")
    assert pb.snapshot()["kie"]["state"] == pb.CLOSED


@pytest.mark.asyncio
async def test_half_open_probe_failure_reopens(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "provider_breaker_cooldown_s", 0.0)
    for _ in range(3):
        pb.note_failure("kie", status=500)
    await pb.acquire("kie")
    assert pb.snapshot()["kie"]["state"] == pb.HALF_OPEN

    pb.note_failure("kie", status=502)
    assert pb.snapshot()["kie"]["state"] == pb.OPEN


def test_providers_are_independent() -> None:
    for _ in range(3):
        pb.note_failure("kie", status=500)
    assert pb.snapshot()["kie"]["state"] == pb.OPEN
    assert "vibecode" not in pb.snapshot()


# ── Retry-After ──────────────────────────────────────────────────────────


def test_parse_retry_after() -> None:
    assert pb.parse_retry_after("30") == 30.0
    assert pb.parse_retry_after(12) == 12.0
    assert pb.parse_retry_after(None) is None
    assert pb.parse_retry_after("Wed, 21 Oct 2026 07:28:00 GMT") is None
    assert pb.parse_retry_after("-5") is None
    # Час внутри одного шага ждать бессмысленно — обрезаем.
    assert pb.parse_retry_after("9999") == 300.0


def test_retry_after_beats_exponential_backoff() -> None:
    pb.note_failure("kie", status=429, retry_after=45.0)
    delay = pb.suggest_delay("kie", attempt=1, default=2.0)
    assert 40.0 < delay <= 45.0


def test_without_retry_after_default_backoff_wins() -> None:
    pb.note_failure("kie", status=500)
    assert pb.suggest_delay("kie", attempt=3, default=8.0) == 8.0


def test_success_clears_retry_after() -> None:
    pb.note_failure("kie", status=429, retry_after=60.0)
    pb.note_success("kie")
    assert pb.suggest_delay("kie", attempt=1, default=2.0) == 2.0


# ── выключатель ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_disabled_flag_restores_old_behaviour(monkeypatch: pytest.MonkeyPatch) -> None:
    """PROVIDER_BREAKER_ENABLED=false — прежние слепые ретраи."""
    monkeypatch.setattr(settings, "provider_breaker_enabled", False)
    for _ in range(50):
        pb.note_failure("kie", status=500)
    await pb.acquire("kie")  # не бросает
    assert pb.suggest_delay("kie", attempt=1, default=2.0) == 2.0
