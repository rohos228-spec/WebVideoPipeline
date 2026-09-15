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

VIBECODE_DEFAULT_ID = "claude-opus-5-vibecode"
VIBECODE_DEFAULT_API_MODEL = "claude-opus-5"

CATALOG: list[dict[str, str]] = [
    # Anthropic — через /v1/messages (anthropic_messages.is_anthropic_model).
    {
        "id": "claude-opus-5-vibecode",
        "provider": "vibecode",
        "group": "Anthropic",
        "label": "Claude Opus 5",
        "site": "vibecode.moe",
        "api_model": "claude-opus-5",
    },
    {
        "id": "claude-sonnet-5-vibecode",
        "provider": "vibecode",
        "group": "Anthropic",
        "label": "Claude Sonnet 5",
        "site": "vibecode.moe",
        "api_model": "claude-sonnet-5",
    },
    {
        "id": "claude-opus-4-8-vibecode",
        "provider": "vibecode",
        "group": "Anthropic",
        "label": "Claude Opus 4.8",
        "site": "vibecode.moe",
        "api_model": "claude-opus-4-8",
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
        "id": "gpt-5.6-sol-vibecode",
        "provider": "vibecode",
        "group": "OpenAI",
        "label": "GPT 5.6 Sol",
        "site": "vibecode.moe",
        "api_model": "gpt-5.6-sol",
    },
    {
        "id": "gpt-5.6-terra-vibecode",
        "provider": "vibecode",
        "group": "OpenAI",
        "label": "GPT 5.6 Terra",
        "site": "vibecode.moe",
        "api_model": "gpt-5.6-terra",
    },
    {
        "id": "gpt-5.6-luna-vibecode",
        "provider": "vibecode",
        "group": "OpenAI",
        "label": "GPT 5.6 Luna",
        "site": "vibecode.moe",
        "api_model": "gpt-5.6-luna",
    },
    {
        "id": "gpt-5.5-vibecode",
        "provider": "vibecode",
        "group": "OpenAI",
        "label": "GPT 5.5",
        "site": "vibecode.moe",
        "api_model": "gpt-5.5",
    },
    # Google
    {
        "id": "gemini-3.1-pro-vibecode",
        "provider": "vibecode",
        "group": "Google",
        "label": "Gemini 3.1 Pro",
        "site": "vibecode.moe",
        "api_model": "gemini-3.1-pro-preview",
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
        "id": "grok-4-6-vibecode",
        "provider": "vibecode",
        "group": "xAI",
        "label": "Grok 4.6",
        "site": "vibecode.moe",
        "api_model": "grok-4-6",
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

_PROVIDERS = frozenset({"kie", "vibecode"})
#: Провайдеры, выведенные из текстового контура: значение в .env / choice.json
#: не должно ронять старт — уводим на kie с предупреждением.
_RETIRED_PROVIDERS = frozenset(
    {"minimax", "hailuo", "m3", "tokenrouter", "kimi", "kimi-k3", "kimi_k3", "moonshot"}
)
_MODEL_ALIASES = {
    "claude": "claude-opus-5-vibecode",
    "opus": "claude-opus-5-vibecode",
    "opus-5": "claude-opus-5-vibecode",
    "claude-opus-5": "claude-opus-5-vibecode",
    "claude-opus-5-vibecode": "claude-opus-5-vibecode",
    "sonnet": "claude-sonnet-5-vibecode",
    "sonnet-5": "claude-sonnet-5-vibecode",
    "claude-sonnet-5": "claude-sonnet-5-vibecode",
    "claude-sonnet-5-vibecode": "claude-sonnet-5-vibecode",
    "claude-opus-4-8": "claude-opus-4-8-vibecode",
    "claude-opus-4.8": "claude-opus-4-8-vibecode",
    "claude-opus-4-8-vibecode": "claude-opus-4-8-vibecode",
    "fable": "claude-fable-5-vibecode",
    "claude-fable-5": "claude-fable-5-vibecode",
    "claude-fable-5-vibecode": "claude-fable-5-vibecode",
    "gpt-5.5": "gpt-5.5-vibecode",
    "gpt-5.5-vibecode": "gpt-5.5-vibecode",
    "gpt-5.6-sol": "gpt-5.6-sol-vibecode",
    # GPT_MODEL по умолчанию пишут через дефис — не терять дефолт .env.
    "gpt-5-6-sol": "gpt-5.6-sol-vibecode",
    "gpt-5.6-sol-vibecode": "gpt-5.6-sol-vibecode",
    "gpt-5.6-terra": "gpt-5.6-terra-vibecode",
    "gpt-5-6-terra": "gpt-5.6-terra-vibecode",
    "gpt-5.6-terra-vibecode": "gpt-5.6-terra-vibecode",
    "gpt-5.6-luna": "gpt-5.6-luna-vibecode",
    "gpt-5-6-luna": "gpt-5.6-luna-vibecode",
    "gpt-5.6-luna-vibecode": "gpt-5.6-luna-vibecode",
    "gemini-3.1-pro": "gemini-3.1-pro-vibecode",
    "gemini-3.1-pro-preview": "gemini-3.1-pro-vibecode",
    "gemini-3.1-pro-vibecode": "gemini-3.1-pro-vibecode",
    "gemini-3-flash": "gemini-3-flash-vibecode",
    "gemini-3-flash-preview": "gemini-3-flash-vibecode",
    "gemini-3-flash-vibecode": "gemini-3-flash-vibecode",
    "grok-4-6": "grok-4-6-vibecode",
    "grok-4.6": "grok-4-6-vibecode",
    "grok-4-6-vibecode": "grok-4-6-vibecode",
    "gpt-kie": "gpt-kie",
}


def _choice_path(cfg: Settings) -> Path:
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
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception as e:  # noqa: BLE001
        logger.warning("text_llm_choice: read failed: {}", e)
        return {}


def write_choice(
    *,
    provider: str,
    model_id: str | None = None,
    cfg: Settings | None = None,
) -> dict[str, Any]:
    s = cfg or settings
    provider = (provider or "kie").strip().lower()
    if provider in {"vibe", "vibecode.moe", "anthropic", "claude"}:
        provider = "vibecode"
    aliased = catalog_item(model_id)
    if aliased:
        provider = aliased["provider"]
        model_id = aliased["id"]
    if provider not in _PROVIDERS:
        raise ValueError(f"unknown text LLM provider: {provider!r}")
    if provider == "vibecode":
        model_id = model_id or VIBECODE_DEFAULT_ID
    else:
        model_id = model_id or "gpt-kie"
    payload = {"provider": provider, "model_id": model_id}
    path = _choice_path(s)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    logger.info("text_llm_choice: active → {} ({})", payload["provider"], payload["model_id"])
    return payload


def resolve_active_provider(cfg: Settings | None = None) -> str:
    """kie по умолчанию; vibecode — по явному выбору.

    `minimax` (2026-08-26) и `tokenrouter`/`kimi` (2026-09) больше не
    провайдеры: старое значение в choice.json / TEXT_LLM_PROVIDER молча
    уводит на kie-дефолт с предупреждением, а не роняет старт.
    """
    s = cfg or settings
    raw_choice = str(read_choice(s).get("provider") or "").strip().lower()
    if raw_choice in {"vibecode", "vibe", "anthropic", "claude"}:
        return "vibecode"
    if raw_choice in {"kie", "gpt", "openai"}:
        return "kie"
    raw = (s.text_llm_provider or "kie").strip().lower()
    if raw in {"vibecode", "vibe", "anthropic", "claude"}:
        return "vibecode"
    if raw in _RETIRED_PROVIDERS or raw_choice in _RETIRED_PROVIDERS:
        logger.warning(
            "text_llm: провайдер {!r} выведен — активен kie; переключи на vibecode",
            raw or raw_choice,
        )
    return "kie"


def resolve_active_model_id(cfg: Settings | None = None) -> str:
    s = cfg or settings
    raw = str(read_choice(s).get("model_id") or "").strip()
    item = catalog_item(raw)
    if item:
        return item["id"]
    prov = resolve_active_provider(s)
    if prov == "vibecode":
        return VIBECODE_DEFAULT_ID
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
        else:
            model = s.gpt_model
            key_ok = bool((s.gpt_api_key or "").strip() or (s.grsai_api_key or "").strip())
            base = s.gpt_base_url or s.grsai_base_url
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
    else:
        label = f"GPT · kie.ai ({s.gpt_model})"
        active_model = s.gpt_model
    return {
        "active_provider": active,
        "active_label": label,
        "active_model": active_model,
        "models": models,
    }
