"""Участнику студии — без сумм в деньгах: чат оркестратора, слой действий, уведомления.

Себестоимость вызовов (оценки шагов, траты провайдеров, потолки, остатки бюджета,
общие траты студии за день) участнику не показывается — тот же принцип, что
``app/services/action_layer/plan.py::member_view`` в отчётах. Логика задач
(одобрения, риск, потолки) работает на исходных данных: меняется только то, что
уходит в браузер участника.

Три слоя, чтобы новое поле не протекло молча:

* события с деньгами по смыслу (``validator_result``, ``checkpoint``) — список
  разрешённого: участник получает только то, что нужно интерфейсу;
* ключи — по шаблону (``*_usd``, ``cost*``, ``spend*``, ``spent*``, ``budget*``,
  ``cap*``, ``remaining*``, ``price*``, ``credit*``, ``forecast*``), а не списком имён;
* текст — сумма с ``$`` / ``US$`` / ``USD`` / «долл.» с любой стороны числа,
  включая «$.42», «0.42$», «10 USD», «48.20 USD».

``MemberMoneyMiddleware`` применяет то же к JSON-ответам (и ошибкам) ручек
``/api/orchestrator``, ``/api/action-layer`` и ``/api/chat-notices``; SSE чата
маскирует ``orchestrator_chat._sse`` по событию.
"""

from __future__ import annotations

import json
import re
from typing import Any

MEMBER_COST_TEXT = "цена видна админу студии"
MEMBER_AMOUNT_TEXT = "(сумма видна админу)"

#: Ключи с деньгами по имени. Шаблон, а не список: новое поле `*_usd` закрыто сразу.
_MONEY_KEY_RE = re.compile(
    r"usd|^cost|_cost|^spend|_spend|^spent|_spent|^budget|_budget|^caps?$|^cap_|_caps?$|"
    r"^remaining|_remaining|^price|_price|^credit|_credit|^forecast|_forecast|^amount|_amount",
    re.IGNORECASE,
)

#: Что участник видит из события проверки: имя, итог, блокирует ли. Подробности
#: (траты дня, потолки, остатки бюджета) — нет.
_VALIDATOR_ALLOWED = frozenset({"name", "pass", "passed", "blocking", "task_id"})
#: Чекпоинт плана: позиция и id правок — да, снимок трат — нет.
_CHECKPOINT_ALLOWED = frozenset(
    {"checkpoint_id", "step_id", "position", "change_ids", "plan", "task_id", "summary", "context"}
)
#: Совпадают с шаблоном, но денег не несут: предупреждения «шаг потратит деньги позже» текстом.
_NOT_MONEY_KEYS = frozenset({"future_spend", "future_spend_effects", "spends_money"})

_CUR = r"(?:US\s?\$|\$|USD\b|usd\b|долл(?:ар\w*|\.)?)"
_NUM = r"(?:\d[\d   ]*(?:[.,]\d+)?|[.,]\d+)"
_AMOUNT_RE = re.compile(
    r"(?:US\s?\$|\$|USD|usd)\s?" + _NUM + r"|" + _NUM + r"\s?" + _CUR,
    re.IGNORECASE,
)


def is_money_key(key: Any) -> bool:
    return isinstance(key, str) and key not in _NOT_MONEY_KEYS and bool(_MONEY_KEY_RE.search(key))


def mask_amounts(text: str) -> str:
    if not text:
        return text
    return _AMOUNT_RE.sub(MEMBER_AMOUNT_TEXT, text)


def member_safe(value: Any) -> Any:
    """Значение без денег: ключи по шаблону убраны, суммы в тексте скрыты."""
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if key == "cost_text":
                out[key] = MEMBER_COST_TEXT
                continue
            if is_money_key(key):
                continue
            out[key] = member_safe(item)
        return out
    if isinstance(value, (list, tuple)):
        return [member_safe(item) for item in value]
    if isinstance(value, str):
        return mask_amounts(value)
    return value


def member_event_payload(event_type: str, payload: Any) -> Any:
    """Payload события чата для участника: сначала список разрешённого по типу, потом маска."""
    if isinstance(payload, dict):
        if event_type == "validator_result":
            payload = {k: v for k, v in payload.items() if k in _VALIDATOR_ALLOWED}
        elif event_type == "checkpoint":
            payload = {k: v for k, v in payload.items() if k in _CHECKPOINT_ALLOWED}
    return member_safe(payload)


_MASKED_PREFIXES = ("/api/orchestrator/", "/api/action-layer", "/api/chat-notices")


class MemberMoneyMiddleware:
    """JSON-ответы (и ошибки) ручек чата, слоя действий и уведомлений — без денег для участника.

    Стоит ВНУТРИ ``IdentityMiddleware``: личность запроса уже известна. SSE
    (``text/event-stream``) не трогает — его маскирует ``orchestrator_chat._sse``.
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http" or not str(scope.get("path", "")).startswith(_MASKED_PREFIXES):
            await self.app(scope, receive, send)
            return
        from app.services.action_layer.plan import member_view

        if not member_view():
            await self.app(scope, receive, send)
            return

        start: dict[str, Any] | None = None
        chunks: list[bytes] = []
        passthrough = False

        async def _send(message) -> None:
            nonlocal start, passthrough
            if message["type"] == "http.response.start":
                headers = dict(message.get("headers") or [])
                ctype = headers.get(b"content-type", b"").decode("latin-1").lower()
                if "application/json" not in ctype:
                    passthrough = True
                    await send(message)
                    return
                start = message
                return
            if message["type"] == "http.response.body" and not passthrough and start is not None:
                chunks.append(message.get("body", b""))
                if message.get("more_body"):
                    return
                body = b"".join(chunks)
                try:
                    body = json.dumps(member_safe(json.loads(body or b"null")), ensure_ascii=False).encode(
                        "utf-8"
                    )
                except ValueError:
                    pass
                headers = [(k, v) for k, v in start.get("headers") or [] if k.lower() != b"content-length"]
                headers.append((b"content-length", str(len(body)).encode("latin-1")))
                await send({**start, "headers": headers})
                await send({"type": "http.response.body", "body": body, "more_body": False})
                return
            await send(message)

        await self.app(scope, receive, _send)
