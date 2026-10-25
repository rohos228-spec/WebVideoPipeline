"""Участнику студии — без сумм в деньгах: чат оркестратора, слой действий, уведомления.

Себестоимость вызовов (оценки шагов, траты провайдеров, потолки, остатки бюджета,
общие траты студии за день) участнику не показывается — тот же принцип, что
``app/services/action_layer/plan.py::member_view`` в отчётах. Логика задач
(одобрения, риск, потолки) работает на исходных данных: меняется только то, что
уходит в браузер участника.

Слои:

* события с деньгами по смыслу (``validator_result``, ``checkpoint``) — список
  разрешённого: участник получает только то, что нужно интерфейсу;
* ключи — только суммы в $: слово ``usd`` в имени (``*_usd``, ``usd_*``), а в чате
  ещё ``cost_estimate``/``estimated_cost`` и числовой ``cost``. Кредиты — единица
  биллинга самого участника (``cost.{low,high,actual}``, ``credits_*``) — остаются;
  ``params``/``params_schema`` и каталог действий не трогаются;
* текст — сумма с ``$`` / ``US$`` / ``USD`` / ``€`` / «долл.», «дол.», «баксов», «у.е.»
  с любой стороны числа, включая «$.42», «0.42$», «1,000.50 USD», «$100k».

``MemberMoneyMiddleware`` применяет то же к JSON-ответам (и ошибкам) ручек
``/api/orchestrator``, ``/api/action-layer``, ``/api/change-control`` и ``/api/chat-notices``; SSE чата
маскирует ``orchestrator_chat._sse`` по событию.
"""

from __future__ import annotations

import json
import re
from typing import Any

MEMBER_COST_TEXT = "цена видна админу студии"
MEMBER_AMOUNT_TEXT = "(сумма видна админу)"

#: Ключи с суммой в $ по имени: ``usd`` отдельным словом (``*_usd``, ``usd_*``, ``usd``).
#: Кредиты (``credits_*``, ``cost.{low,high}``) — единица биллинга самого участника, их не трогаем.
_USD_KEY_RE = re.compile(r"(?:^|_)usd(?:$|_)", re.IGNORECASE)
#: Чат оркестратора: суммы в $ без слова usd в имени (карточка, план, итог шага).
_CHAT_MONEY_KEYS = frozenset({"cost_estimate", "estimated_cost"})
#: Поддеревья, где ключи — имена параметров и данные человека, а не деньги студии.
_NEVER_MASK = frozenset({"params_schema", "params"})

#: Что участник видит из события проверки: имя, итог, блокирует ли. Подробности
#: (траты дня, потолки, остатки бюджета) — нет.
_VALIDATOR_ALLOWED = frozenset({"name", "pass", "passed", "blocking", "task_id"})
#: Чекпоинт плана: позиция и id правок — да, снимок трат — нет.
_CHECKPOINT_ALLOWED = frozenset(
    {"checkpoint_id", "step_id", "position", "change_ids", "plan", "task_id", "summary", "context"}
)

_NUM = (
    r"(?:\d{1,3}(?:[   ,]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?|[.,]\d+)"
    r"(?:\s?(?:k|m|тыс\.?|млн\.?)(?![a-zа-я]))?"
)
_CUR_BEFORE = r"(?:US\s?\$|\$|€|USD|EUR)"
_CUR_AFTER = r"(?:US\s?\$|\$|€|USD\b|EUR\b|долл(?:ар\w*|\.)?|дол\.|бакс\w*|у\.\s?е\.?)"
_AMOUNT_RE = re.compile(
    _CUR_BEFORE + r"\s?" + _NUM + r"|" + _NUM + r"\s?" + _CUR_AFTER,
    re.IGNORECASE,
)


def is_money_key(key: Any, *, chat: bool = False) -> bool:
    if not isinstance(key, str):
        return False
    return bool(_USD_KEY_RE.search(key)) or (chat and key in _CHAT_MONEY_KEYS)


def mask_amounts(text: str) -> str:
    if not text:
        return text
    return _AMOUNT_RE.sub(MEMBER_AMOUNT_TEXT, text)


def member_safe(value: Any, *, chat: bool = False) -> Any:
    """Значение без сумм в $ для участника.

    Везде: ключи со словом ``usd`` убраны, суммы с валютой в тексте скрыты, параметры
    (``params``, ``params_schema``) не трогаются. ``chat=True`` — события и ответы чата
    оркестратора: ещё ``cost_estimate``/``estimated_cost`` и числовой ``cost`` (там это $),
    ``cost_text`` заменён пометкой.
    """
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if key in _NEVER_MASK:
                # Чат: в params шага плана планировщик кладёт и estimated_cost в $ — его участнику
                # не отдаём; остальные параметры (и всё в слое действий / каталоге) как есть.
                if chat and key == "params" and isinstance(item, dict):
                    out[key] = {k: v for k, v in item.items() if not is_money_key(k, chat=True)}
                else:
                    out[key] = item
                continue
            if chat and key == "cost_text":
                out[key] = MEMBER_COST_TEXT
                continue
            if is_money_key(key, chat=chat):
                continue
            if chat and key == "cost" and isinstance(item, (int, float)) and not isinstance(item, bool):
                continue
            out[key] = member_safe(item, chat=chat)
        return out
    if isinstance(value, (list, tuple)):
        return [member_safe(item, chat=chat) for item in value]
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
    return member_safe(payload, chat=True)


_MASKED_PREFIXES = ("/api/orchestrator/", "/api/action-layer", "/api/chat-notices", "/api/change-control")
#: Каталог действий — имена параметров и схемы, денег в нём нет.
_NOT_MASKED = ("/api/action-layer/catalog",)


class MemberMoneyMiddleware:
    """JSON-ответы (и ошибки) ручек чата, слоя действий и уведомлений — без денег для участника.

    Стоит ВНУТРИ ``IdentityMiddleware``: личность запроса уже известна. SSE
    (``text/event-stream``) не трогает — его маскирует ``orchestrator_chat._sse``.
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        path = str(scope.get("path", ""))
        if scope["type"] != "http" or not path.startswith(_MASKED_PREFIXES) or path.startswith(_NOT_MASKED):
            await self.app(scope, receive, send)
            return
        chat = path.startswith("/api/orchestrator/")
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
                    body = json.dumps(
                        member_safe(json.loads(body or b"null"), chat=chat), ensure_ascii=False
                    ).encode("utf-8")
                except ValueError:
                    pass
                out_headers: list[tuple[Any, Any]] = [
                    (k, v) for k, v in start.get("headers") or [] if k.lower() != b"content-length"
                ]
                out_headers.append((b"content-length", str(len(body)).encode("latin-1")))
                await send({**start, "headers": out_headers})
                await send({"type": "http.response.body", "body": body, "more_body": False})
                return
            await send(message)

        await self.app(scope, receive, _send)


#: События шины, которые несут уведомления чата: их WebSocket режет по арендатору и маскирует.
NOTICE_EVENTS = frozenset({"chat_notice", "chat_notice_read"})


def ws_event_for_viewer(event: Any) -> Any | None:
    """Событие шины для WebSocket текущего зрителя. ``None`` — этому зрителю не слать.

    Уведомления чата идут и в общий канал ``global``: чужому арендатору они не
    отправляются (метка ``tenant_id`` ставится при публикации), участнику — без сумм.
    Метка арендатора наружу не уходит. Прочие события шины не трогаются.
    """
    if not isinstance(event, dict) or event.get("type") not in NOTICE_EVENTS:
        return event
    from app.services.action_layer.plan import member_view
    from app.services.tenant import current_tenant

    owner = event.get("tenant_id")
    viewer = current_tenant()
    if viewer is not None and owner != viewer:
        return None
    out = {k: v for k, v in event.items() if k != "tenant_id"}
    return member_safe(out) if member_view() else out
