"""Локальная маршрутизация моделей, которые реально открыты ключами 52pay.

Ключ пустой — маршрут не срабатывает, запрос остаётся на vibecode/kie.
Модели Клода идут на рабочий ключ pay52_claude_low_key (sk-AFaYVF...).
"""

from __future__ import annotations

from dataclasses import dataclass

# catalog id или id из /v1/models → (поле Settings, id в запросе)
_TEXT: dict[str, tuple[str, str]] = {
    # DeepSeek
    "deepseek-v4.1-flash": ("pay52_deepseek_key", "deepseek-v4.1-flash"),
    "deepseek-v4.1-flash-pay52": ("pay52_deepseek_key", "deepseek-v4.1-flash"),
    # Kimi
    "kimi-k3.1-pro": ("pay52_kimi_key", "kimi-k3.1-pro"),
    "kimi-k3.1-pro-pay52": ("pay52_kimi_key", "kimi-k3.1-pro"),
    # Gemini
    "gemini-4-argon": ("pay52_gemini_key", "gemini-4-argon"),
    "gemini-4-argon-pay52": ("pay52_gemini_key", "gemini-4-argon"),
    "gemini-3.8-flash": ("pay52_gemini_key", "gemini-3.8-flash"),
    "gemini-3.8-flash-pay52": ("pay52_gemini_key", "gemini-3.8-flash"),
    "gemini-flash-lite-latest": ("pay52_gemini_key", "gemini-flash-lite-latest"),
    "gemini-flash-lite-latest-pay52": ("pay52_gemini_key", "gemini-flash-lite-latest"),
    # OpenAI / GPT-6
    "gpt-6.1-sol": ("pay52_gpt_key", "gpt-6.1-sol"),
    "gpt-6.1-sol-pay52": ("pay52_gpt_key", "gpt-6.1-sol"),
    "gpt-6-sol": ("pay52_gpt_key", "gpt-6.1-sol"),
    "gpt-6-astra": ("pay52_gpt_key", "gpt-6-astra"),
    "gpt-6-astra-pay52": ("pay52_gpt_key", "gpt-6-astra"),
    "gpt-6-luna": ("pay52_gpt_key", "gpt-6-luna"),
    "gpt-6-luna-pay52": ("pay52_gpt_key", "gpt-6-luna"),
    "gpt-6.1-terra": ("pay52_gpt_key", "gpt-6.1-terra"),
    "gpt-6.1-terra-pay52": ("pay52_gpt_key", "gpt-6.1-terra"),
    "gpt-5.6-sol": ("pay52_gpt_key", "gpt-6.1-sol"),
    "gpt-5-6-sol": ("pay52_gpt_key", "gpt-6.1-sol"),
    "gpt-5.5": ("pay52_gpt_key", "gpt-6.1-sol"),
    # Claude (Anthropic) — на актуальном рабочем ключе sk-AFaYVF...
    "claude-sonnet-5": ("pay52_claude_low_key", "claude-sonnet-5"),
    "claude-sonnet-5-pay52": ("pay52_claude_low_key", "claude-sonnet-5"),
    "claude-sonnet-5-pay52-low": ("pay52_claude_low_key", "claude-sonnet-5"),
    "claude-sonnet-5-pay52-high": ("pay52_claude_low_key", "claude-sonnet-5"),
    "claude-sonnet-5-5": ("pay52_claude_low_key", "claude-sonnet-5"),
    "claude-sonnet-5-5-pay52-high": ("pay52_claude_low_key", "claude-sonnet-5"),
    "claude-sonnet-5-5-pay52-low": ("pay52_claude_low_key", "claude-sonnet-5"),
    "claude-haiku-5-5": ("pay52_claude_low_key", "claude-haiku-5-5"),
    "claude-haiku-5-5-pay52": ("pay52_claude_low_key", "claude-haiku-5-5"),
    "claude-haiku-5-5-pay52-high": ("pay52_claude_low_key", "claude-haiku-5-5"),
    "claude-haiku-4-5": ("pay52_claude_low_key", "claude-haiku-5-5"),
    "claude-haiku-4-5-pay52-low": ("pay52_claude_low_key", "claude-haiku-5-5"),
    "claude-opus-5-5": ("pay52_claude_low_key", "claude-opus-5-5"),
    "claude-opus-5-5-pay52": ("pay52_claude_low_key", "claude-opus-5-5"),
    "claude-opus-5-5-pay52-high": ("pay52_claude_low_key", "claude-opus-5-5"),
    "claude-opus-5-5-pay52-low": ("pay52_claude_low_key", "claude-opus-5-5"),
    "claude-opus-5": ("pay52_claude_low_key", "claude-opus-5-5"),
    "claude-fable-5-1": ("pay52_claude_low_key", "claude-fable-5-1"),
    "claude-fable-5-1-pay52": ("pay52_claude_low_key", "claude-fable-5-1"),
    "claude-fable-5-1-pay52-high": ("pay52_claude_low_key", "claude-fable-5-1"),
    # GLM (Zhipu)
    "glm-5.3": ("pay52_glm_key", "glm-5.3"),
    "glm-5.3-pay52": ("pay52_glm_key", "glm-5.3"),
    "glm-5.3-flash": ("pay52_glm_key", "glm-5.3-flash"),
    "glm-5.3-flash-pay52": ("pay52_glm_key", "glm-5.3-flash"),
    "glm-5.2": ("pay52_glm_key", "glm-5.3"),
}

# Видео этих ключей не совпадает с kie/outsee id в пикере. Карта для явного вызова.
_VIDEO: dict[str, str] = {
    "seedance-2.0-mini": "seedance_fast_key",
    "seedance-2.0-mini-ref": "seedance_fast_key",
    "seedance-2-0-mini": "seedance_fast_key",
    "seedance 2.0 fast 海外": "seedance_fast_key",
    "seedance-2.0-fast": "seedance_fast_key",
    "seedance-2-0-fast": "seedance_fast_key",
    "seedance-2-0-fast-overseas": "seedance_fast_key",
    "seedance-2.5": "seedance_fast_key",
    "seedance-2-5": "seedance_fast_key",
    "seedance 2.5 海外": "seedance_fast_key",
    "kling-4.0-k1": "seedance_fast_key",
    "veo-3.1-fast-generate-preview": "seedance_fast_key",
}


def _key(settings, field: str) -> str:
    """Ключ из Settings: имя нашей линии (*_api_key) или новой (pay52_gpt_key и т.п.)."""
    for name in (field, field.replace("_api_key", "_key"), field.replace("_key", "_api_key")):
        value = str(getattr(settings, name, "") or "").strip()
        if value:
            return value
    if "claude" in field:
        # Fallback на любой настроенный ключ Клода
        return str(getattr(settings, "pay52_claude_low_key", "") or getattr(settings, "pay52_claude_high_key", "") or "").strip()
    if "seedance" in field:
        return str(getattr(settings, "seedance_fast_key", "") or getattr(settings, "seedance_mini_key", "") or getattr(settings, "seedance_overseas_key", "") or "").strip()
    return ""


@dataclass(frozen=True)
class Pay52TextRoute:
    api_model: str
    api_key: str
    url: str

    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }


def resolve_text(model: str | None) -> Pay52TextRoute | None:
    raw = (model or "").strip()
    spec = _TEXT.get(raw) or _TEXT.get(raw.lower())
    if not spec:
        return None
    from app.settings import settings

    field, api_model = spec
    key = _key(settings, field)
    if not key:
        return None
    base = (settings.pay52_base_url or "https://api.52pay.com/v1").strip().rstrip("/")
    return Pay52TextRoute(api_model=api_model, api_key=key, url=f"{base}/chat/completions")


def video_api_key(model: str | None) -> str:
    raw = (model or "").strip()
    from app.settings import settings

    field = _VIDEO.get(raw) or _VIDEO.get(raw.lower())
    if not field:
        return ""
    return _key(settings, field)
