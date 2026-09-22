"""Транспорт Anthropic Messages: Claude через vibecode.moe.

vibecode отдаёт `claude-*` ТОЛЬКО на `/v1/messages` (формат Anthropic) —
на `/v1/chat/completions` шлюз отвечает 400 «this model is only available
via /v1/messages». Поэтому Claude идёт не OpenAI-совместимым путём
`gpt_api`, а официальным SDK `anthropic` с `base_url` шлюза. Тело по-прежнему
собирает `gpt_api.build_messages` (OpenAI-форма) — здесь оно переводится в
Messages, ответ заворачивается в тот же `GptChatResult`, что и у остальных
транспортов: леджер, брейкер, проверка «ответила другая модель» и контракты
работают без изменений.

Живые пробы 2026-08-26 через vibecode:
* auth принимает и `x-api-key` (то, что шлёт SDK), и `Authorization: Bearer`;
* `claude-opus-5` / `claude-sonnet-5` возвращают своё же имя в `model` —
  `_check_served_model` проходит; `claude-haiku-4-5` отдаёт датированный id
  и оборачивает JSON в ```json, `claude-fable-5` — 502 upstream, поэтому в
  каталог студии заведены только Opus 5 и Sonnet 5;
* SSE на `/v1/messages` работает (message_start … message_stop);
* `temperature` на Opus 5 / Sonnet 5 API отвергает (400) — не передаём.
"""

from __future__ import annotations

import asyncio
import re
from typing import TYPE_CHECKING, Any

import anthropic
from loguru import logger

from app.settings import settings

if TYPE_CHECKING:
    from app.services.gpt_api import GptChatResult, ResponseSchema

# Стриминг снимает HTTP-таймаут, поэтому потолок можно держать щедрым:
# apply-ops на десятки кадров — длинный ответ, и обрез по max_tokens в
# контрактном режиме стоит целого ретрая.
MAX_TOKENS = 32_000

_DATA_URL_RE = re.compile(r"^data:(?P<mime>[^;,]+);base64,(?P<data>.+)$", re.S)

#: Блоки нативного tool-calling: в истории они идут как есть (см. `_content_block`).
TOOL_BLOCK_TYPES: frozenset[str] = frozenset({"tool_use", "tool_result"})


def is_anthropic_model(model: str | None) -> bool:
    return (model or "").strip().lower().startswith("claude")


def messages_base_url(base: str) -> str:
    """База для SDK: он сам добавляет `/v1/messages`, хвост `/v1` снять.

    `VIBECODE_BASE_URL=https://vibecode.moe/v1` — иначе получилось бы
    `/v1/v1/messages`.
    """
    b = (base or "").strip().rstrip("/")
    if b.lower().endswith("/v1"):
        b = b[:-3].rstrip("/")
    return b


def messages_url(base: str) -> str:
    """Полный URL для логов и леджера (relay = netloc)."""
    return f"{messages_base_url(base)}/v1/messages"


def _content_block(part: Any) -> dict[str, Any]:
    if isinstance(part, str):
        return {"type": "text", "text": part}
    if not isinstance(part, dict):
        return {"type": "text", "text": str(part)}
    kind = str(part.get("type") or "")
    if kind == "text":
        return {"type": "text", "text": str(part.get("text") or "")}
    if kind in TOOL_BLOCK_TYPES:
        # Нативный tool-calling (агент студии): блоки уже в формате Messages,
        # переводить нечего. `tool_use` — просьба модели, `tool_result` —
        # наш ответ ей; оба должны доехать до API как есть, иначе история
        # разговора с инструментами не восстановится.
        return dict(part)
    if kind == "image_url":
        raw = part.get("image_url")
        url = str((raw or {}).get("url") if isinstance(raw, dict) else raw or "")
        m = _DATA_URL_RE.match(url)
        if m:
            return {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": m.group("mime"),
                    "data": m.group("data").replace("\n", ""),
                },
            }
        return {"type": "image", "source": {"type": "url", "url": url}}
    # input_file / прочее OpenAI-специфичное: в chat-режиме gpt_api их не
    # шлёт (PDF идёт текстом), но молча терять контент нельзя.
    return {"type": "text", "text": str(part.get("text") or part)}


def convert_messages(messages: list[dict[str, Any]]) -> tuple[str | None, list[dict[str, Any]]]:
    """OpenAI `messages` → (`system`, Messages `messages`).

    Системные сообщения уходят в top-level `system`; первым сообщением
    Messages требует `user` — если история начинается с ответа ассистента,
    подставляется короткая затравка, а не 400 от API.
    """
    system_parts: list[str] = []
    out: list[dict[str, Any]] = []
    for m in messages or []:
        role = str(m.get("role") or "user")
        content = m.get("content")
        if role == "system":
            if isinstance(content, list):
                system_parts.append("\n".join(str(_content_block(p).get("text") or "") for p in content))
            elif content:
                system_parts.append(str(content))
            continue
        if role not in {"user", "assistant"}:
            role = "user"
        if isinstance(content, list):
            blocks = [_content_block(p) for p in content]
            out.append({"role": role, "content": blocks})
        else:
            out.append({"role": role, "content": str(content or "")})
    if out and out[0]["role"] != "user":
        out.insert(0, {"role": "user", "content": "(начало диалога)"})
    system = "\n\n".join(p for p in system_parts if p.strip()) or None
    return system, out


def build_request(
    body: dict[str, Any],
    *,
    response_schema: ResponseSchema | None = None,
    structured: bool = False,
    max_tokens: int = MAX_TOKENS,
) -> dict[str, Any]:
    """Тело chat/completions (из `gpt_api`) → аргументы `messages.stream`.

    `temperature` не переносится: Opus 5 / Sonnet 5 отвечают на него 400.
    `tools`/`tool_choice` переносятся как есть — они уже в формате Messages.
    `response_format` из OpenAI-тела игнорируется — схема прикрепляется
    через `output_config.format`, и только когда релей в allowlist
    `GPT_STRUCTURED_RELAYS` (`structured=True`); иначе, как и у остальных
    транспортов, формат держат промт и клиентская валидация контрактов.
    """
    system, messages = convert_messages(list(body.get("messages") or []))
    req: dict[str, Any] = {
        "model": str(body.get("model") or ""),
        "max_tokens": int(max_tokens),
        "messages": messages,
    }
    if system:
        req["system"] = system
    # Нативные инструменты: схемы уже в формате Messages
    # ({name, description, input_schema}) — их собирает агент студии.
    if body.get("tools"):
        req["tools"] = list(body["tools"])
        if body.get("tool_choice"):
            req["tool_choice"] = body["tool_choice"]
    if structured and response_schema is not None:
        req["output_config"] = {
            "format": {"type": "json_schema", "schema": response_schema.schema},
        }
    return req


def usage_dict(usage: Any) -> dict[str, Any]:
    """usage Messages → ключи обеих веток учёта (`llm_ledger._pick`)."""
    if usage is None:
        return {}
    get = (lambda k: getattr(usage, k, None)) if not isinstance(usage, dict) else usage.get
    inp = int(get("input_tokens") or 0)
    outp = int(get("output_tokens") or 0)
    out: dict[str, Any] = {
        "input_tokens": inp,
        "output_tokens": outp,
        "prompt_tokens": inp,
        "completion_tokens": outp,
        "total_tokens": inp + outp,
    }
    for k in ("cache_read_input_tokens", "cache_creation_input_tokens"):
        v = get(k)
        if isinstance(v, int):
            out[k] = v
    return out


def _client(*, base_url: str, api_key: str, timeout: float) -> anthropic.AsyncAnthropic:
    # Ретраи выключены: у gpt_api свой цикл ретраев + брейкер per-провайдер,
    # двойные ретраи давали бы 3×3 попыток на лежащем шлюзе.
    # read=None — тот же принцип, что в gpt_api._stream_timeout: ждём SSE,
    # пока сервер сам его не закроет; idle-предохранитель только если задан.
    configured = float(getattr(settings, "gpt_stream_read_timeout_s", 0.0) or 0.0)
    read: float | None = max(configured, 120.0) if configured > 0 else None
    _ = timeout
    return anthropic.AsyncAnthropic(
        api_key=api_key,
        base_url=messages_base_url(base_url),
        timeout=anthropic.Timeout(None, connect=30.0, read=read, write=None, pool=30.0),
        max_retries=0,
    )


async def chat_messages(
    *,
    base_url: str,
    api_key: str,
    body: dict[str, Any],
    timeout: float,
    use_model: str,
    response_schema: ResponseSchema | None = None,
    structured: bool = False,
    on_delta: Any | None = None,
) -> GptChatResult:
    """POST /v1/messages (стрим) → `GptChatResult`.

    Ошибки переводятся в `GptApiError` той же классификацией, что HTTP-коды
    остальных транспортов (`_raise_http_status`): 429/5xx — retryable с
    `retry_after_s` для брейкера, 4xx — fatal. `stop_reason=refusal` —
    отдельный fatal-вид `refusal`, `max_tokens` — `finish_reason=length`.

    `on_delta` — тот же контракт, что у `gpt_api._chat_completions_stream`:
    колбэк получает кусок текста по мере генерации, может быть синхронным
    или корутинной функцией, а его ошибки не роняют разбор ответа (итог
    всё равно берётся из финального message). Без него чат студии на Claude
    не видел ни одного токена до конца генерации.
    """
    from app.services.gpt_api import GptApiError, GptChatResult, _raise_http_status

    req = build_request(body, response_schema=response_schema, structured=structured)
    client = _client(base_url=base_url, api_key=api_key, timeout=timeout)
    try:
        async with client:
            async with client.messages.stream(**req) as stream:
                if on_delta is not None:
                    async for piece in stream.text_stream:
                        if not piece:
                            continue
                        try:
                            res = on_delta(piece)
                            if asyncio.iscoroutine(res):
                                await res
                        except Exception:  # noqa: BLE001 — колбэк UI не ломает разбор
                            pass
                msg = await stream.get_final_message()
    except anthropic.APIStatusError as e:
        err_body = e.body if isinstance(e.body, str) else str(e.body or e.message)
        headers = getattr(getattr(e, "response", None), "headers", None) or {}
        _raise_http_status(
            int(e.status_code), err_body, use_model=use_model, retry_after=headers.get("retry-after")
        )
        raise  # pragma: no cover — _raise_http_status бросает на любом status>=400
    except anthropic.APITimeoutError as e:
        raise GptApiError(
            f"Claude Messages: таймаут ({e})",
            context={"retryable": True, "error_kind": "timeout", "model": use_model},
        ) from e
    except anthropic.APIConnectionError as e:
        raise GptApiError(
            f"Claude Messages: сеть ({e})",
            context={"retryable": True, "error_kind": "network", "model": use_model},
        ) from e

    blocks = [_block_to_dict(b) for b in (msg.content or [])]
    text = "".join(str(b.get("text") or "") for b in blocks if b.get("type") == "text")
    tool_calls = [b for b in blocks if b.get("type") == "tool_use"]
    stop = str(msg.stop_reason or "")
    if stop == "refusal":
        details = getattr(msg, "stop_details", None)
        category = getattr(details, "category", None) if details is not None else None
        raise GptApiError(
            f"Claude Messages: модель отказала (refusal, category={category!r})",
            context={"retryable": False, "error_kind": "refusal", "model": use_model},
        )
    # Ответ из одних tool_use — штатный, когда инструменты были в запросе:
    # модель просит инструмент, текста ей говорить незачем. Но релей отдавал
    # `stop_reason=tool_use` с пустым content и на запрос БЕЗ инструментов
    # (прод 2026-08-27, сжатие промта персонажа CH02): вызывающий получил ""
    # и дальше сжимал пустоту — в генератор ушёл выдуманный промт, референсом
    # персонажа стала стоковая картинка Excel. Для запроса без инструментов
    # такой ответ — пустой, и он ретраится.
    if (not text.strip() or text.strip() in ("...", "…")) and not (tool_calls and req.get("tools")):
        raise GptApiError(
            f"Claude Messages: пустой/заглушечный output '{text}' (stop_reason={stop or '-'})",
            context={"retryable": True, "error_kind": "empty_stream", "model": use_model},
        )
    finish = {
        "end_turn": "stop",
        "max_tokens": "length",
        "stop_sequence": "stop",
        "tool_use": "tool_use",
    }.get(stop, stop)
    if finish == "length":
        logger.warning("Claude Messages: ответ упёрся в max_tokens={} model={}", req["max_tokens"], use_model)
    return GptChatResult(
        text=text,
        model=use_model,
        finish_reason=finish,
        usage=usage_dict(getattr(msg, "usage", None)),
        raw={
            "id": msg.id,
            "model": msg.model,
            "stop_reason": stop,
            "transport": "anthropic_messages",
            # Блоки ответа целиком: агенту нужны tool_use с их id, а не
            # только склеенный текст.
            "content": blocks,
        },
        response_id=str(msg.id or ""),
        served_model=str(msg.model or ""),
    )


def _block_to_dict(block: Any) -> dict[str, Any]:
    """Блок ответа SDK → словарь. Текст и tool_use; прочее — текстом, не молча."""
    kind = str(getattr(block, "type", "") or "")
    if kind == "text":
        return {"type": "text", "text": str(getattr(block, "text", "") or "")}
    if kind == "tool_use":
        raw_input = getattr(block, "input", None)
        return {
            "type": "tool_use",
            "id": str(getattr(block, "id", "") or ""),
            "name": str(getattr(block, "name", "") or ""),
            "input": dict(raw_input) if isinstance(raw_input, dict) else {},
        }
    return {"type": "text", "text": str(getattr(block, "text", "") or "")}
