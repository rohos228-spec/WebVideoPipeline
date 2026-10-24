"""Единая точка платформенных ключей для исходящих HTTP.

Call-site не читает ``settings.*_api_key`` напрямую для платных вызовов —
идёт через ``resolve(provider, purpose)``. kie и vibecode никогда не
путаются: на vibecode не подставляется GPT/KIE-ключ и наоборот.

При ``GPT_RELAY_TOKEN`` + VPS-base для текстового kie-пути (и purpose
``relay``) в заголовки добавляется ``X-VP-Relay-Token``.
"""

from __future__ import annotations

from typing import TypedDict

from app.settings import Settings

__all__ = [
    "ProviderKey",
    "ProviderKeyError",
    "KNOWN_PROVIDERS",
    "resolve",
    "configured",
]


class ProviderKey(TypedDict):
    api_key: str
    base_url: str
    headers: dict[str, str]


class ProviderKeyError(ValueError):
    """Нет ключа / неизвестный провайдер — без тихой подстановки чужого ключа."""

    def __init__(self, message: str, *, provider: str, purpose: str = "") -> None:
        super().__init__(message)
        self.provider = provider
        self.purpose = purpose


KNOWN_PROVIDERS = frozenset(
    {
        "gpt",
        "kie",
        "vibecode",
        "outsee",
        "wavespeed",
        "elevenlabs",
        "minimax",
        "pay52",
        "text",  # alias → активный текстовый LLM
    }
)

_DEFAULT_VIBECODE_BASE = "https://vibecode.moe/v1"
_DEFAULT_KIE_BASE = "https://api.kie.ai"
_DEFAULT_OUTSEE_BASE = "https://outsee.io"
_DEFAULT_WAVESPEED_BASE = "https://api.wavespeed.ai"
_DEFAULT_ELEVEN_BASE = "https://api.elevenlabs.io"
_DEFAULT_MINIMAX_BASE = "https://api.minimax.io"
_DEFAULT_PAY52_BASE = "https://api.52pay.com/v1"


def _cfg(cfg: Settings | None) -> Settings:
    if cfg is not None:
        return cfg
    # Live lookup — tests monkeypatch ``app.settings.settings``.
    from app.settings import settings as live

    return live


def _bearer(key: str, *, extra: dict[str, str] | None = None) -> dict[str, str]:
    h: dict[str, str] = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    if extra:
        h.update(extra)
    return h


def _maybe_relay_header(s: Settings, base: str, headers: dict[str, str]) -> dict[str, str]:
    """Добавить X-VP-Relay-Token, если бьём именно в настроенный VPS-relay."""
    token = (s.gpt_relay_token or "").strip()
    vps = s.vps_relay_base_url
    if not token or not vps:
        return headers
    if base.rstrip("/") != vps.rstrip("/"):
        return headers
    out = dict(headers)
    out["X-VP-Relay-Token"] = token
    return out


def _resolve_active_text(s: Settings, purpose: str) -> ProviderKey:
    """Активный текстовый LLM (kie | vibecode | pay52) через effective_*."""
    # purpose=relay форсирует VPS-base для kie-пути, если токен задан.
    if purpose == "relay":
        vps = s.vps_relay_base_url
        if not vps:
            raise ProviderKeyError(
                "GPT_RELAY_TOKEN/GPT_BASE_URL для VPS-relay не настроены",
                provider="gpt",
                purpose=purpose,
            )
        # Relay — для kie/gpt текста; vibecode/pay52 на relay не уводим сюда.
        if s.text_llm_is_vibecode:
            return _resolve_vibecode(s, "relay")
        if s.text_llm_is_pay52:
            return _resolve_pay52(s)
        key = (s.gpt_api_key or "").strip()
        headers = _maybe_relay_header(s, vps, _bearer(key) if key else {})
        return {"api_key": key, "base_url": vps, "headers": headers}

    key = (s.gpt_api_effective_key or "").strip()
    base = (s.gpt_api_effective_base_url or "").strip().rstrip("/")
    headers = _bearer(key) if key else {}
    headers = _maybe_relay_header(s, base, headers)
    return {"api_key": key, "base_url": base, "headers": headers}


def _resolve_vibecode(s: Settings, purpose: str) -> ProviderKey:
    """Только VIBECODE_API_KEY — никогда GPT_API_KEY / KIE_API_KEY."""
    key = (s.vibecode_api_key or "").strip()
    if purpose == "relay":
        vps = s.vps_relay_base_url
        base = (vps or (s.vibecode_base_url or _DEFAULT_VIBECODE_BASE)).strip().rstrip("/")
    else:
        base = (s.vibecode_base_url or _DEFAULT_VIBECODE_BASE).strip().rstrip("/")
    headers = _bearer(key) if key else {}
    if purpose == "relay":
        headers = _maybe_relay_header(s, base, headers)
    return {"api_key": key, "base_url": base, "headers": headers}


def _resolve_kie(s: Settings, purpose: str) -> ProviderKey:
    """Kie Market / jobs. Ключ: KIE_API_KEY, фолбэк GPT_API_KEY (не vibecode)."""
    if purpose in ("text", "relay", "chat"):
        # Текстовый путь через active/effective (kie-провайдер).
        return _resolve_active_text(s, purpose if purpose == "relay" else "text")

    key = (s.kie_api_key or "").strip()
    if not key:
        # Legacy: тот же аккаунт, что GPT на kie.ai. vibecode сюда не берём.
        key = (s.gpt_api_key or "").strip()
    base = (s.kie_api_base_url or "").strip().rstrip("/")
    if not base:
        gpt_base = (s.gpt_base_url or "").strip().rstrip("/")
        if "kie.ai" in gpt_base.lower():
            base = gpt_base.split("/codex")[0].rstrip("/") or _DEFAULT_KIE_BASE
        else:
            base = _DEFAULT_KIE_BASE
    headers = _bearer(key) if key else {}
    return {"api_key": key, "base_url": base, "headers": headers}


def _resolve_pay52(s: Settings) -> ProviderKey:
    if s.text_llm_is_pay52:
        key = (s.gpt_api_effective_key or "").strip()
    else:
        key = (
            s.pay52_gpt_key
            or s.pay52_glm_key
            or s.pay52_claude_low_key
            or s.pay52_claude_high_key
            or ""
        ).strip()
    base = (s.pay52_base_url or _DEFAULT_PAY52_BASE).strip().rstrip("/")
    headers = _bearer(key) if key else {}
    return {"api_key": key, "base_url": base, "headers": headers}


def _resolve_outsee(s: Settings) -> ProviderKey:
    key = (s.outsee_api_key or "").strip()
    base = (s.outsee_api_base_url or _DEFAULT_OUTSEE_BASE).strip().rstrip("/")
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    } if key else {}
    return {"api_key": key, "base_url": base, "headers": headers}


def _resolve_wavespeed(s: Settings) -> ProviderKey:
    key = (s.wavespeed_api_key or "").strip()
    base = (s.wavespeed_api_base_url or _DEFAULT_WAVESPEED_BASE).strip().rstrip("/")
    headers = _bearer(key) if key else {}
    return {"api_key": key, "base_url": base, "headers": headers}


def _resolve_elevenlabs(s: Settings) -> ProviderKey:
    key = (s.elevenlabs_api_key or "").strip()
    base = _DEFAULT_ELEVEN_BASE
    # ElevenLabs принимает xi-api-key, не Bearer.
    headers = {
        "xi-api-key": key,
        "Content-Type": "application/json",
    } if key else {}
    return {"api_key": key, "base_url": base, "headers": headers}


def _resolve_minimax(s: Settings) -> ProviderKey:
    key = (s.minimax_api_key or "").strip()
    base = (s.minimax_base_url or _DEFAULT_MINIMAX_BASE).strip().rstrip("/")
    headers = _bearer(key) if key else {}
    return {"api_key": key, "base_url": base, "headers": headers}


def resolve(
    provider: str,
    purpose: str = "default",
    *,
    cfg: Settings | None = None,
    require_key: bool = False,
) -> ProviderKey:
    """Вернуть ``{api_key, base_url, headers}`` для исходящего HTTP.

    Parameters
    ----------
    provider:
        ``gpt`` / ``text`` — активный текстовый LLM (effective_*).
        ``vibecode`` — только vibecode (картинки/текст), без kie-ключа.
        ``kie`` — Market/jobs (или text/relay → active text).
        ``outsee``, ``wavespeed``, ``elevenlabs``, ``minimax``, ``pay52``.
    purpose:
        ``default`` | ``text`` | ``relay`` | ``image`` | ``video`` | ``audio`` |
        ``market`` | ``chat``. Для vibecode ``relay`` = vibecode-ключ + VPS base.
    require_key:
        Если True и ключ пуст — ``ProviderKeyError`` (без чужой подстановки).
    """
    s = _cfg(cfg)
    p = (provider or "").strip().lower()
    purpose_l = (purpose or "default").strip().lower() or "default"

    if p not in KNOWN_PROVIDERS:
        raise ProviderKeyError(
            f"неизвестный провайдер ключей: {provider!r}",
            provider=p,
            purpose=purpose_l,
        )

    if p in ("gpt", "text"):
        creds = _resolve_active_text(s, purpose_l)
    elif p == "vibecode":
        creds = _resolve_vibecode(s, purpose_l)
    elif p == "kie":
        creds = _resolve_kie(s, purpose_l)
    elif p == "pay52":
        creds = _resolve_pay52(s)
    elif p == "outsee":
        creds = _resolve_outsee(s)
    elif p == "wavespeed":
        creds = _resolve_wavespeed(s)
    elif p == "elevenlabs":
        creds = _resolve_elevenlabs(s)
    elif p == "minimax":
        creds = _resolve_minimax(s)
    else:  # pragma: no cover
        raise ProviderKeyError(f"неизвестный провайдер: {provider!r}", provider=p)

    if require_key and not (creds.get("api_key") or "").strip():
        env_hint = {
            "gpt": "GPT_API_KEY / VIBECODE_API_KEY / PAY52_*",
            "text": "GPT_API_KEY / VIBECODE_API_KEY / PAY52_*",
            "vibecode": "VIBECODE_API_KEY",
            "kie": "KIE_API_KEY / GPT_API_KEY",
            "outsee": "OUTSEE_API_KEY",
            "wavespeed": "WAVESPEED_API_KEY",
            "elevenlabs": "ELEVENLABS_API_KEY",
            "minimax": "MINIMAX_API_KEY",
            "pay52": "PAY52_*",
        }.get(p, p.upper() + "_API_KEY")
        raise ProviderKeyError(
            f"{p}: нет API-ключа ({env_hint})",
            provider=p,
            purpose=purpose_l,
        )
    return creds


def configured(provider: str, purpose: str = "default", *, cfg: Settings | None = None) -> bool:
    """Есть ли непустой ключ у провайдера (без исключения)."""
    try:
        creds = resolve(provider, purpose, cfg=cfg, require_key=False)
    except ProviderKeyError:
        return False
    return bool((creds.get("api_key") or "").strip())
