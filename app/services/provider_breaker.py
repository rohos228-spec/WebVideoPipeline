"""Circuit breaker + лимитер темпа per-провайдер (п.16-17 плана техдолга).

Что было. Ретраи есть в четырёх слоях (петля `gpt_api.chat`, chunk-retry
PDF, `outsee_retry`, `step_failure_policy` — 3 цикла × 3 с паузой 30 мин),
но все они слепые:

* 429 не отличался от 500 — лимит провайдера лечился тем же экспоненциальным
  бэкоффом, что и упавший шлюз;
* заголовок ``Retry-After`` не читался вообще, хотя провайдер прямо говорит,
  сколько ждать;
* лежащий провайдер выгребал полный набор попыток на КАЖДОМ вызове: сорок
  кадров подряд по 4 попытки с бэкоффом — это десятки минут в пустоту и
  оплаченные неуспешные вызовы (их считает `llm_ledger`).

Что здесь. Немного состояния на провайдера:

* **брейкер** — N подряд «инфраструктурных» отказов (5xx / сеть / таймаут)
  переводят провайдера в ``open`` на cooldown. В ``open`` вызов падает сразу
  `ProviderCircuitOpen`, не тратя ни времени, ни денег. По истечении
  cooldown — ``half_open``: пропускается ОДИН пробный вызов; успех
  закрывает, отказ снова открывает;
* **Retry-After** — если провайдер прислал заголовок, `suggest_delay`
  возвращает его вместо экспоненты;
* **лимитер темпа** — минимальный интервал между вызовами одного провайдера
  (`PROVIDER_MIN_INTERVAL_MS`, по умолчанию 0 = выключен).

Осознанно НЕ трогаем: 429 не считается отказом для брейкера. Лимит — это
«приходи позже», а не «провайдер лежит»; открывать на нём цепь значило бы
останавливать конвейер там, где достаточно подождать.

Состояние процессное (in-memory): воркер один, переживать рестарт этой
информации не нужно — при старте честнее попробовать заново.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

from loguru import logger

CLOSED = "closed"
OPEN = "open"
HALF_OPEN = "half_open"


class ProviderCircuitOpen(RuntimeError):
    """Цепь провайдера разомкнута — вызов не делается."""

    def __init__(self, provider: str, retry_in_s: float) -> None:
        super().__init__(
            f"провайдер {provider} временно отключён брейкером "
            f"(ещё ~{retry_in_s:.0f} с): подряд идут инфраструктурные отказы"
        )
        self.provider = provider
        self.retry_in_s = retry_in_s


@dataclass
class _State:
    failures: int = 0
    opened_at: float = 0.0
    state: str = CLOSED
    last_call_at: float = 0.0
    retry_after_until: float = 0.0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


_states: dict[str, _State] = {}


def _cfg() -> tuple[bool, int, float, float]:
    from app.settings import settings

    return (
        bool(getattr(settings, "provider_breaker_enabled", True)),
        int(getattr(settings, "provider_breaker_failures", 5)),
        float(getattr(settings, "provider_breaker_cooldown_s", 60.0)),
        float(getattr(settings, "provider_min_interval_ms", 0)) / 1000.0,
    )


def _state(provider: str) -> _State:
    key = (provider or "unknown").strip().lower()
    st = _states.get(key)
    if st is None:
        st = _State()
        _states[key] = st
    return st


def reset(provider: str | None = None) -> None:
    """Сбросить состояние (провайдера или всё) — для тестов и ручного вмешательства."""
    if provider is None:
        _states.clear()
        return
    _states.pop((provider or "").strip().lower(), None)


def snapshot() -> dict[str, dict[str, Any]]:
    """Состояние всех провайдеров — для диагностики / дашборда."""
    now = time.monotonic()
    _enabled, threshold, cooldown, _interval = _cfg()
    out: dict[str, dict[str, Any]] = {}
    for name, st in _states.items():
        out[name] = {
            "state": st.state,
            "failures": st.failures,
            "threshold": threshold,
            "opens_for_s": round(max(0.0, st.opened_at + cooldown - now), 1) if st.state == OPEN else 0.0,
            "retry_after_s": round(max(0.0, st.retry_after_until - now), 1),
        }
    return out


def is_infrastructure_failure(status: int | None, error_kind: str = "") -> bool:
    """Считать ли отказ «провайдер лежит».

    429 намеренно НЕ считается: это «приходи позже», лечится Retry-After,
    а не размыканием цепи.
    """
    if error_kind in {"network", "timeout"}:
        return True
    if status is None:
        return False
    return int(status) >= 500


def parse_retry_after(value: Any) -> float | None:
    """`Retry-After` в секундах. Формат HTTP-date не поддерживаем — релеи шлют число."""
    if value is None:
        return None
    try:
        seconds = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    if seconds < 0:
        return None
    # Провайдер может прислать заведомо большое значение; ждать час внутри
    # одного шага бессмысленно — обрезаем, дальше решает step_failure_policy.
    return min(seconds, 300.0)


async def acquire(provider: str) -> None:
    """Проверить цепь и выдержать минимальный интервал. Открытая цепь → исключение."""
    enabled, _threshold, cooldown, min_interval = _cfg()
    if not enabled:
        return
    st = _state(provider)
    async with st.lock:
        now = time.monotonic()

        if st.state == OPEN:
            left = st.opened_at + cooldown - now
            if left > 0:
                raise ProviderCircuitOpen(provider, left)
            st.state = HALF_OPEN
            logger.info("provider_breaker: {} → half_open, пробный вызов", provider)

        # Провайдер просил подождать (429/503 с Retry-After).
        wait = st.retry_after_until - now
        if wait > 0:
            logger.info("provider_breaker: {} — Retry-After, ждём {:.1f} с", provider, wait)
            await asyncio.sleep(wait)
            now = time.monotonic()

        if min_interval > 0:
            gap = st.last_call_at + min_interval - now
            if gap > 0:
                await asyncio.sleep(gap)
                now = time.monotonic()
        st.last_call_at = now


def note_success(provider: str) -> None:
    enabled, _threshold, _cooldown, _interval = _cfg()
    if not enabled:
        return
    st = _state(provider)
    if st.state != CLOSED or st.failures:
        logger.info("provider_breaker: {} → closed", provider)
    st.failures = 0
    st.state = CLOSED
    st.retry_after_until = 0.0


def note_failure(
    provider: str,
    *,
    status: int | None = None,
    error_kind: str = "",
    retry_after: float | None = None,
) -> None:
    """Учесть отказ. 429 двигает только Retry-After, цепь не размыкает."""
    enabled, threshold, cooldown, _interval = _cfg()
    if not enabled:
        return
    st = _state(provider)
    now = time.monotonic()

    if retry_after is not None:
        st.retry_after_until = max(st.retry_after_until, now + retry_after)

    if not is_infrastructure_failure(status, error_kind):
        return

    st.failures += 1
    if st.state == HALF_OPEN:
        st.state = OPEN
        st.opened_at = now
        logger.warning(
            "provider_breaker: {} — пробный вызов упал, снова open на {:.0f} с", provider, cooldown
        )
        return
    if st.failures >= threshold and st.state == CLOSED:
        st.state = OPEN
        st.opened_at = now
        logger.warning(
            "provider_breaker: {} → open на {:.0f} с ({} инфраструктурных отказов подряд)",
            provider,
            cooldown,
            st.failures,
        )


def suggest_delay(provider: str, attempt: int, *, default: float) -> float:
    """Пауза перед следующей попыткой: Retry-After провайдера важнее экспоненты."""
    enabled, _threshold, _cooldown, _interval = _cfg()
    if not enabled:
        return default
    st = _state(provider)
    left = st.retry_after_until - time.monotonic()
    if left > 0:
        return left
    return default
