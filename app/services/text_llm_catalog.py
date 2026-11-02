"""Каталог текстовых LLM: Claude / GPT / Gemini / Grok на vibecode + GPT (kie).

Выбор активной модели: data/text_llm_choice.json (Studio UI / `/api/text-llm`).
GPT_* / VIBECODE_* в .env не затираются.

vibecode: Claude идёт через `/v1/messages` (`app/services/anthropic_messages.py`),
остальные — через chat/completions. Дефолт vibecode — Claude Opus 5.
MiniMax из текстового контура выведен 2026-08-26 (решение владельца),
TokenRouter/Kimi — 2026-09 (шлюз мёртв, ключи выпилены из настроек).

Каждая запись несёт `group` — семейство модели для группировки в пикере
Студии (`models[].group` в `/api/text-llm`). Состав сверен с живым снимком
`vibecode_models_snapshot.json`: модели не из снимка сюда не заводятся,
иначе выбор в UI даёт 400 на первом же вызове.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from loguru import logger

from app.settings import Settings, settings

_CHOICE_NAME = "text_llm_choice.json"

VIBECODE_DEFAULT_ID = "gpt-5.6-sol-vibecode"
VIBECODE_DEFAULT_API_MODEL = "gpt-5.6-sol"

CATALOG: list[dict[str, str]] = [
    # Anthropic — через /v1/messages (anthropic_messages.is_anthropic_model).
    {
        "id": "claude-sonnet-5-5-vibecode",
        "provider": "vibecode",
        "group": "Anthropic",
        "label": "Claude Sonnet 5.5",
        "site": "vibecode.moe",
        "api_model": "claude-sonnet-5-5",
    },
    {
        "id": "claude-opus-5-5-vibecode",
        "provider": "vibecode",
        "group": "Anthropic",
        "label": "Claude Opus 5.5",
        "site": "vibecode.moe",
        "api_model": "claude-opus-5-5",
    },
    {
        "id": "claude-haiku-4-5-vibecode",
        "provider": "vibecode",
        "group": "Anthropic",
        "label": "Claude Haiku 4.5",
        "site": "vibecode.moe",
        "api_model": "claude-haiku-4-5",
    },
    {
        "id": "claude-fable-5-1-vibecode",
        "provider": "vibecode",
        "group": "Anthropic",
        "label": "Claude Fable 5.1",
        "site": "vibecode.moe",
        "api_model": "claude-fable-5-1",
    },
    {
        "id": "claude-fable-5-vibecode",
        "provider": "vibecode",
        "group": "Anthropic",
        "label": "Claude Fable 5",
        "site": "vibecode.moe",
        "api_model": "claude-fable-5",
    },
    # OpenAI
    {
        "id": "gpt-6-sol-vibecode",
        "provider": "vibecode",
        "group": "OpenAI",
        "label": "GPT 6 Sol",
        "site": "vibecode.moe",
        "api_model": "gpt-6-sol",
    },
    {
        "id": "gpt-6.1-sol-vibecode",
        "provider": "vibecode",
        "group": "OpenAI",
        "label": "GPT 6.1 Sol",
        "site": "vibecode.moe",
        "api_model": "gpt-6.1-sol",
    },
    {
        "id": "gpt-6-astra-vibecode",
        "provider": "vibecode",
        "group": "OpenAI",
        "label": "GPT 6 Astra",
        "site": "vibecode.moe",
        "api_model": "gpt-6-astra",
    },
    {
        "id": "gpt-5.6-sol-vibecode",
        "provider": "vibecode",
        "group": "OpenAI",
        "label": "GPT 5.6 Sol",
        "site": "vibecode.moe",
        "api_model": "gpt-5.6-sol",
    },
    {
        "id": "gpt-6-luna-vibecode",
        "provider": "vibecode",
        "group": "OpenAI",
        "label": "GPT 6 Luna",
        "site": "vibecode.moe",
        "api_model": "gpt-6-luna",
    },
    # Google
    {
        "id": "gemini-3.8-flash-vibecode",
        "provider": "vibecode",
        "group": "Google",
        "label": "Gemini 3.8 Flash",
        "site": "vibecode.moe",
        "api_model": "gemini-3.8-flash",
    },
    {
        "id": "gemini-3-flash-vibecode",
        "provider": "vibecode",
        "group": "Google",
        "label": "Gemini 3 Flash",
        "site": "vibecode.moe",
        "api_model": "gemini-3-flash-preview",
    },
    # xAI
    {
        "id": "grok-4-7-vibecode",
        "provider": "vibecode",
        "group": "xAI",
        "label": "Grok 4.7",
        "site": "vibecode.moe",
        "api_model": "grok-4-7",
    },
    # GLM
    {
        "id": "glm-5.3-flash-vibecode",
        "provider": "vibecode",
        "group": "GLM",
        "label": "GLM 5.3 Flash",
        "site": "vibecode.moe",
        "api_model": "glm-5.3-flash",
    },
    {
        "id": "glm-5.3-flashx-vibecode",
        "provider": "vibecode",
        "group": "GLM",
        "label": "GLM 5.3 FlashX",
        "site": "vibecode.moe",
        "api_model": "glm-5.3-flashx",
    },
    # Cloud LLM (GLM / Claude Low / Claude High / GPT)
    {
        "id": "glm-5.3-flash-pay52",
        "provider": "pay52",
        "group": "GLM",
        "label": "GLM 5.3 Flash",
        "site": "Cloud API",
        "api_model": "glm-5.3-flash",
    },
    {
        "id": "glm-5.3-pay52",
        "provider": "pay52",
        "group": "GLM",
        "label": "GLM 5.3",
        "site": "Cloud API",
        "api_model": "glm-5.3",
    },
    {
        "id": "glm-5.2-pay52",
        "provider": "pay52",
        "group": "GLM",
        "label": "GLM 5.2",
        "site": "Cloud API",
        "api_model": "glm-5.2",
    },
    # Claude Low (Cost-effective / Эконом)
    {
        "id": "claude-haiku-4-5-pay52-low",
        "provider": "pay52",
        "group": "Anthropic",
        "label": "Claude Haiku 4.5 (Эконом)",
        "site": "Cloud API",
        "api_model": "claude-haiku-4-5-20251001",  # gitleaks:allow — имя модели API, не секрет
    },
    {
        "id": "claude-sonnet-4-5-pay52-low",
        "provider": "pay52",
        "group": "Anthropic",
        "label": "Claude Sonnet 4.5 (Эконом)",
        "site": "Cloud API",
        "api_model": "claude-sonnet-4-5-20250929",
    },
    {
        "id": "claude-sonnet-5-pay52-low",
        "provider": "pay52",
        "group": "Anthropic",
        "label": "Claude Sonnet 5 (Эконом)",
        "site": "Cloud API",
        "api_model": "claude-sonnet-5",
    },
    {
        "id": "claude-sonnet-5-5-pay52-low",
        "provider": "pay52",
        "group": "Anthropic",
        "label": "Claude Sonnet 5.5 (Эконом)",
        "site": "Cloud API",
        "api_model": "claude-sonnet-5-5",
    },
    {
        "id": "claude-opus-5-pay52-low",
        "provider": "pay52",
        "group": "Anthropic",
        "label": "Claude Opus 5 (Эконом)",
        "site": "Cloud API",
        "api_model": "claude-opus-5",
    },
    {
        "id": "claude-opus-5-5-pay52-low",
        "provider": "pay52",
        "group": "Anthropic",
        "label": "Claude Opus 5.5 (Эконом)",
        "site": "Cloud API",
        "api_model": "claude-opus-5-5",
    },
    # Claude High (Code Max / Full-blood / Высокое качество)
    {
        "id": "claude-fable-5-1-pay52-high",
        "provider": "pay52",
        "group": "Anthropic",
        "label": "Claude Fable 5.1 (High Quality)",
        "site": "Cloud API",
        "api_model": "claude-fable-5-1",
    },
    {
        "id": "claude-fable-5-pay52-high",
        "provider": "pay52",
        "group": "Anthropic",
        "label": "Claude Fable 5 (High Quality)",
        "site": "Cloud API",
        "api_model": "claude-fable-5",
    },
    {
        "id": "claude-haiku-5-5-pay52-high",
        "provider": "pay52",
        "group": "Anthropic",
        "label": "Claude Haiku 5.5 (High Quality)",
        "site": "Cloud API",
        "api_model": "claude-haiku-5-5",
    },
    {
        "id": "claude-sonnet-5-pay52-high",
        "provider": "pay52",
        "group": "Anthropic",
        "label": "Claude Sonnet 5 (High Quality)",
        "site": "Cloud API",
        "api_model": "claude-sonnet-5",
    },
    {
        "id": "claude-sonnet-5-5-pay52-high",
        "provider": "pay52",
        "group": "Anthropic",
        "label": "Claude Sonnet 5.5 (High Quality)",
        "site": "Cloud API",
        "api_model": "claude-sonnet-5-5",
    },
    {
        "id": "claude-opus-5-pay52-high",
        "provider": "pay52",
        "group": "Anthropic",
        "label": "Claude Opus 5 (High Quality)",
        "site": "Cloud API",
        "api_model": "claude-opus-5",
    },
    {
        "id": "claude-opus-5-5-pay52-high",
        "provider": "pay52",
        "group": "Anthropic",
        "label": "Claude Opus 5.5 (High Quality)",
        "site": "Cloud API",
        "api_model": "claude-opus-5-5",
    },
    {
        "id": "gpt-5.6-sol-pay52",
        "provider": "pay52",
        "group": "OpenAI",
        "label": "GPT 5.6 Sol",
        "site": "Cloud API",
        "api_model": "gpt-5.6-sol",
    },
    {
        "id": "gpt-6-sol-pay52",
        "provider": "pay52",
        "group": "OpenAI",
        "label": "GPT 6 Sol",
        "site": "Cloud API",
        "api_model": "gpt-6-sol",
    },
    {
        "id": "gpt-6.1-sol-pay52",
        "provider": "pay52",
        "group": "OpenAI",
        "label": "GPT 6.1 Sol",
        "site": "Cloud API",
        "api_model": "gpt-6.1-sol",
    },
    {
        "id": "gpt-6-astra-pay52",
        "provider": "pay52",
        "group": "OpenAI",
        "label": "GPT 6 Astra",
        "site": "Cloud API",
        "api_model": "gpt-6-astra",
    },
    # kie.ai — модель берётся из GPT_MODEL, api_model не фиксируем.
    {
        "id": "gpt-kie",
        "provider": "kie",
        "group": "KIE",
        "label": "GPT (kie.ai)",
        "site": "kie.ai",
    },
]

PAY52_DEFAULT_ID = "gpt-6.1-sol-pay52"
PAY52_DEFAULT_API_MODEL = "gpt-6.1-sol"

_PROVIDERS = frozenset({"kie", "vibecode", "pay52"})
#: Провайдеры, выведенные из текстового контура: значение в .env / choice.json
#: не должно ронять старт — уводим на kie с предупреждением.
_RETIRED_PROVIDERS = frozenset(
    {"minimax", "hailuo", "m3", "tokenrouter", "kimi", "kimi-k3", "kimi_k3", "moonshot"}
)
_MODEL_ALIASES = {
    # Anthropic
    "sonnet": "claude-sonnet-5-5-vibecode",
    "sonnet-5": "claude-sonnet-5-5-vibecode",
    "sonnet-5-5": "claude-sonnet-5-5-vibecode",
    "claude-sonnet-5": "claude-sonnet-5-5-vibecode",
    "claude-sonnet-5-5": "claude-sonnet-5-5-vibecode",
    "claude-sonnet-5-5-vibecode": "claude-sonnet-5-5-vibecode",
    "opus": "claude-opus-5-5-vibecode",
    "opus-5": "claude-opus-5-5-vibecode",
    "opus-5-5": "claude-opus-5-5-vibecode",
    "claude-opus-5": "claude-opus-5-5-vibecode",
    "claude-opus-5-5": "claude-opus-5-5-vibecode",
    "claude-opus-5-5-vibecode": "claude-opus-5-5-vibecode",
    "haiku": "claude-haiku-4-5-vibecode",
    "claude-haiku-4-5": "claude-haiku-4-5-vibecode",
    "claude-haiku-4-5-vibecode": "claude-haiku-4-5-vibecode",
    "fable": "claude-fable-5-1-vibecode",
    "claude-fable-5": "claude-fable-5-vibecode",
    "claude-fable-5-vibecode": "claude-fable-5-vibecode",
    "claude-fable-5-1": "claude-fable-5-1-vibecode",
    "claude-fable-5.1": "claude-fable-5-1-vibecode",
    "claude-fable-5-1-vibecode": "claude-fable-5-1-vibecode",
    # OpenAI
    "gpt-6-sol": "gpt-6-sol-vibecode",
    "gpt-6-sol-vibecode": "gpt-6-sol-vibecode",
    "gpt-6.1-sol": "gpt-6.1-sol-vibecode",
    "gpt-6-1-sol": "gpt-6.1-sol-vibecode",
    "gpt-6.1-sol-vibecode": "gpt-6.1-sol-vibecode",
    "gpt-6-astra": "gpt-6-astra-vibecode",
    "gpt-6-astra-vibecode": "gpt-6-astra-vibecode",
    "gpt-5.6-sol": "gpt-5.6-sol-vibecode",
    "gpt-5-6-sol": "gpt-5.6-sol-vibecode",
    "gpt-5.6-sol-vibecode": "gpt-5.6-sol-vibecode",
    "gpt-6-luna": "gpt-6-luna-vibecode",
    "gpt-6-luna-vibecode": "gpt-6-luna-vibecode",
    # Gemini
    "gemini-3.8-flash": "gemini-3.8-flash-vibecode",
    "gemini-3.8-flash-vibecode": "gemini-3.8-flash-vibecode",
    "gemini-3-flash": "gemini-3-flash-vibecode",
    "gemini-3-flash-preview": "gemini-3-flash-vibecode",
    "gemini-3-flash-vibecode": "gemini-3-flash-vibecode",
    # xAI
    "grok-4-7": "grok-4-7-vibecode",
    "grok-4.7": "grok-4-7-vibecode",
    "grok-4-7-vibecode": "grok-4-7-vibecode",
    # GLM
    "glm-5.3-flash": "glm-5.3-flash-vibecode",
    "glm-5.3-flash-vibecode": "glm-5.3-flash-vibecode",
    "glm-5.3-flashx": "glm-5.3-flashx-vibecode",
    "glm-5.3-flashx-vibecode": "glm-5.3-flashx-vibecode",
    # 52pay aliases
    "glm-5.2": "glm-5.2-pay52",
    "glm-5.3": "glm-5.3-pay52",
    "glm-5.2-pay52": "glm-5.2-pay52",
    "glm-5.3-pay52": "glm-5.3-pay52",
    "glm-5.3-flash-pay52": "glm-5.3-flash-pay52",
    "claude-haiku-4-5-pay52": "claude-haiku-4-5-pay52-low",
    "claude-haiku-4-5-pay52-low": "claude-haiku-4-5-pay52-low",
    "claude-sonnet-4-5-pay52": "claude-sonnet-4-5-pay52-low",
    "claude-sonnet-4-5-pay52-low": "claude-sonnet-4-5-pay52-low",
    "claude-sonnet-5-pay52": "claude-sonnet-5-pay52-low",
    "claude-sonnet-5-pay52-low": "claude-sonnet-5-pay52-low",
    "claude-sonnet-5-pay52-high": "claude-sonnet-5-pay52-high",
    "claude-sonnet-5-5-pay52": "claude-sonnet-5-5-pay52-high",
    "claude-sonnet-5-5-pay52-low": "claude-sonnet-5-5-pay52-low",
    "claude-sonnet-5-5-pay52-high": "claude-sonnet-5-5-pay52-high",
    "claude-opus-5-pay52": "claude-opus-5-pay52-low",
    "claude-opus-5-pay52-low": "claude-opus-5-pay52-low",
    "claude-opus-5-pay52-high": "claude-opus-5-pay52-high",
    "claude-opus-5-5-pay52": "claude-opus-5-5-pay52-high",
    "claude-opus-5-5-pay52-low": "claude-opus-5-5-pay52-low",
    "claude-opus-5-5-pay52-high": "claude-opus-5-5-pay52-high",
    "claude-haiku-5-5-pay52": "claude-haiku-5-5-pay52-high",
    "claude-haiku-5-5-pay52-high": "claude-haiku-5-5-pay52-high",
    "claude-fable-5-1-pay52": "claude-fable-5-1-pay52-high",
    "claude-fable-5-1-pay52-high": "claude-fable-5-1-pay52-high",
    "claude-fable-5-pay52": "claude-fable-5-pay52-high",
    "claude-fable-5-pay52-high": "claude-fable-5-pay52-high",
    "gpt-5.6-sol-pay52": "gpt-5.6-sol-pay52",
    "gpt-6-sol-pay52": "gpt-6-sol-pay52",
    "gpt-6.1-sol-pay52": "gpt-6.1-sol-pay52",
    "gpt-6-astra-pay52": "gpt-6-astra-pay52",
    # KIE
    "gpt-kie": "gpt-kie",
}


def _choice_path(cfg: Settings) -> Path:
    from app.services.tenant import current_tenant

    tenant = current_tenant()
    if tenant:
        return Path(cfg.data_dir) / "tenants" / tenant / _CHOICE_NAME
    return Path(cfg.data_dir) / _CHOICE_NAME


def catalog_item(model_id: str | None) -> dict[str, str] | None:
    cid = _MODEL_ALIASES.get((model_id or "").strip())
    if not cid:
        raw = (model_id or "").strip()
        cid = raw if any(it["id"] == raw for it in CATALOG) else ""
    if not cid:
        return None
    return next((it for it in CATALOG if it["id"] == cid), None)


def catalog_api_model(model_id: str | None, *, default: str = VIBECODE_DEFAULT_API_MODEL) -> str:
    item = catalog_item(model_id)
    if item and item.get("api_model"):
        return item["api_model"]
    return default


def read_choice(cfg: Settings | None = None) -> dict[str, Any]:
    s = cfg or settings
    path = _choice_path(s)
    if not path.is_file():
        # Fall back to root choice if tenant hasn't chosen their own model yet
        fallback = Path(s.data_dir) / _CHOICE_NAME
        if fallback.is_file():
            try:
                data = json.loads(fallback.read_text(encoding="utf-8"))
                return data if isinstance(data, dict) else {}
            except Exception:
                return {}
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception as e:  # noqa: BLE001
        logger.warning("text_llm_choice: read failed: {}", e)
        return {}


REASONING_EFFORTS: tuple[str, ...] = ("off", "low", "medium", "high", "xhigh")

REASONING_OPTIONS: list[dict[str, str]] = [
    {"id": "off", "label": "Выкл", "hint": "Мгновенный ответ без скрытых размышлений (быстро)"},
    {"id": "low", "label": "Низкий (low)", "hint": "Быстрое размышление (~5-15 сек), минимальный шанс таймаута"},
    {"id": "medium", "label": "Средний (mid)", "hint": "Взвешенное рассуждение, стандарт для сценариев"},
    {"id": "high", "label": "Высокий (high)", "hint": "Глубокий анализ (может думать до 1-2 минут)"},
    {"id": "xhigh", "label": "Экстра (xhigh)", "hint": "Предельное мышление (для GPT-5.6 / флагманов)"},
]


def normalize_reasoning_effort(raw: Any) -> str:
    """Нормализация уровня мышления в допустимый токен."""
    val = str(raw or "").strip().lower()
    aliases = {
        "выкл": "off",
        "none": "off",
        "0": "off",
        "false": "off",
        "disabled": "off",
        "низкий": "low",
        "середина": "medium",
        "mid": "medium",
        "средний": "medium",
        "высокий": "high",
        "экстра": "xhigh",
        "максимум": "xhigh",
        "extra": "xhigh",
    }
    val = aliases.get(val, val)
    if val in REASONING_EFFORTS:
        return val
    return "off"


def resolve_active_reasoning_effort(cfg: Settings | None = None) -> str:
    """Активный уровень мышления (off | low | medium | high | xhigh)."""
    s = cfg or settings
    choice = read_choice(s)
    if "reasoning_effort" in choice:
        return normalize_reasoning_effort(choice["reasoning_effort"])
    return normalize_reasoning_effort(getattr(s, "gpt_reasoning_effort", "off"))


def write_choice(
    *,
    provider: str | None = None,
    model_id: str | None = None,
    reasoning_effort: str | None = None,
    cfg: Settings | None = None,
) -> dict[str, Any]:
    s = cfg or settings
    existing = read_choice(s)
    default_prov = existing.get("provider") or resolve_active_provider(s)
    provider = (provider or default_prov or "pay52").strip().lower()
    if provider in {"vibe", "vibecode.moe", "anthropic", "claude"}:
        provider = "vibecode"
    elif provider in {"pay52", "52pay", "pay52.com"}:
        provider = "pay52"
    aliased = catalog_item(model_id) if model_id else None
    if aliased:
        provider = aliased["provider"]
        model_id = aliased["id"]
    if provider not in _PROVIDERS:
        raise ValueError(f"unknown text LLM provider: {provider!r}")
    if provider == "vibecode":
        model_id = model_id or existing.get("model_id") or VIBECODE_DEFAULT_ID
    elif provider == "pay52":
        model_id = model_id or existing.get("model_id") or PAY52_DEFAULT_ID
    else:
        model_id = model_id or existing.get("model_id") or "gpt-kie"

    if reasoning_effort is not None:
        effort = normalize_reasoning_effort(reasoning_effort)
    elif "reasoning_effort" in existing:
        effort = normalize_reasoning_effort(existing["reasoning_effort"])
    else:
        effort = normalize_reasoning_effort(getattr(s, "gpt_reasoning_effort", "off"))

    payload = {
        "provider": provider,
        "model_id": model_id,
        "reasoning_effort": effort,
    }
    path = _choice_path(s)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    logger.info(
        "text_llm_choice: active → {} ({}), reasoning_effort={}",
        payload["provider"],
        payload["model_id"],
        effort,
    )
    return payload


def resolve_active_provider(cfg: Settings | None = None) -> str:
    """pay52 по умолчанию; vibecode / kie — по явному выбору.

    `minimax` (2026-08-26) и `tokenrouter`/`kimi` (2026-09) больше не
    провайдеры: старое значение в choice.json / TEXT_LLM_PROVIDER молча
    уводит на pay52-дефолт с предупреждением, а не роняет старт.
    """
    s = cfg or settings
    raw_choice = str(read_choice(s).get("provider") or "").strip().lower()
    if raw_choice in {"vibecode", "vibe", "anthropic", "claude"}:
        return "vibecode"
    if raw_choice in {"pay52", "52pay", "pay52.com"}:
        return "pay52"
    if raw_choice in {"kie", "gpt", "openai"}:
        return "kie"
    raw = (s.text_llm_provider or "pay52").strip().lower()
    if raw in {"vibecode", "vibe", "anthropic", "claude"}:
        return "vibecode"
    if raw in {"pay52", "52pay", "pay52.com"}:
        return "pay52"
    if raw in {"kie", "gpt", "openai"}:
        return "kie"
    if raw in _RETIRED_PROVIDERS or raw_choice in _RETIRED_PROVIDERS:
        logger.warning(
            "text_llm: провайдер {!r} выведен — активен pay52; переключи на vibecode или kie",
            raw or raw_choice,
        )
    return "pay52"


def resolve_active_model_id(cfg: Settings | None = None) -> str:
    s = cfg or settings
    raw = str(read_choice(s).get("model_id") or "").strip()
    item = catalog_item(raw)
    if item:
        return item["id"]
    prov = resolve_active_provider(s)
    if prov == "vibecode":
        return VIBECODE_DEFAULT_ID
    if prov == "pay52":
        return PAY52_DEFAULT_ID
    return "gpt-kie"


def catalog_status(cfg: Settings | None = None) -> dict[str, Any]:
    s = cfg or settings
    active = resolve_active_provider(s)
    active_id = resolve_active_model_id(s)
    models: list[dict[str, Any]] = []
    for item in CATALOG:
        prov = item["provider"]
        if prov == "vibecode":
            model = item.get("api_model") or VIBECODE_DEFAULT_API_MODEL
            key_ok = bool((s.vibecode_api_key or "").strip())
            base = s.vibecode_base_url
        elif prov == "pay52":
            model = item.get("api_model") or PAY52_DEFAULT_API_MODEL
            m_id = item["id"].lower()
            if "glm" in m_id:
                key_ok = bool((s.pay52_glm_key or "").strip())
            elif "high" in m_id or "fable" in m_id or "5-5" in m_id:
                key_ok = bool((s.pay52_claude_high_key or "").strip())
            elif "claude" in m_id:
                key_ok = bool((s.pay52_claude_low_key or "").strip())
            elif "gpt" in m_id:
                key_ok = bool((s.pay52_gpt_key or "").strip())
            else:
                key_ok = bool((s.pay52_gpt_key or s.pay52_glm_key or "").strip())
            base = s.pay52_base_url
        else:
            model = s.gpt_model
            key_ok = bool((s.gpt_api_key or "").strip())
            base = s.gpt_base_url
        models.append(
            {
                **item,
                "model": model,
                "base_url": base,
                "key_configured": key_ok,
                "active": item["id"] == active_id,
            }
        )
    if active == "vibecode":
        active_raw = catalog_item(active_id)
        active_item: dict[str, Any] = active_raw if isinstance(active_raw, dict) else {}
        api_model = active_item.get("api_model") or VIBECODE_DEFAULT_API_MODEL
        pretty = active_item.get("label") or "Claude"
        label = f"{pretty} · vibecode.moe ({api_model})"
        active_model = api_model
    elif active == "pay52":
        active_raw = catalog_item(active_id)
        active_item = active_raw if isinstance(active_raw, dict) else {}
        api_model = active_item.get("api_model") or PAY52_DEFAULT_API_MODEL
        pretty = active_item.get("label") or "Cloud LLM"
        label = f"{pretty} ({api_model})"
        active_model = api_model
    else:
        label = f"GPT · kie.ai ({s.gpt_model})"
        active_model = s.gpt_model
    active_reasoning = resolve_active_reasoning_effort(s)
    return {
        "active_provider": active,
        "active_label": label,
        "active_model": active_model,
        "active_reasoning_effort": active_reasoning,
        "reasoning_options": REASONING_OPTIONS,
        "models": models,
    }
