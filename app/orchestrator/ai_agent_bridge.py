"""Мост окна «ИИ-агент» к движку оркестратора: один чат студии.

Окно «ИИ-агент» (``db_browser.orchestrator_chat_stream``) сначала отдаёт реплику оркестратору:
понимание живых фраз, план из каталога действий, проверки, критик, бюджет, карточки
подтверждения, разбор и починка нод. Ответ и карточка переводятся в формат окна
(``reply`` и ``pending_confirm`` вида ``action`` с токеном ``orch:<change_id>``), так что
дизайн и прежние функции окна не меняются.

Если оркестратор не взялся (не понял, «чат пока не умеет», нет ответа, вложенные файлы) —
реплика идёт прежним путём окна (один вызов GPT с правками кадров, кодом, ветками).
Выключатель — ``AI_AGENT_ORCHESTRATOR=0``.
"""

from __future__ import annotations

import asyncio
import os
import re
import time
from collections.abc import AsyncIterator
from typing import Any

from loguru import logger

TOKEN_PREFIX = "orch:"
TURN_TIMEOUT_S = float(os.environ.get("AI_AGENT_ORCH_TIMEOUT_S", "240"))
DECIDE_TIMEOUT_S = 165.0
_IDLE_AFTER_REPLY_S = 2.5

# Ответы, после которых реплику лучше отдать прежнему движку окна.
_NOT_HANDLED_RE = re.compile(
    r"Не понял, что сделать|^\s*Этого чат пока не умеет\.?\s*$|не умею|Модель сейчас не отвечает", re.I
)
_AUTO_PREFIX_RE = re.compile(r"^\[РЕЖИМ:[^\]]*\]\s*", re.S)
_END_TYPES = {"task_done", "task_failed"}
_YES_RE = re.compile(r"^(?:да|ага|ок|окей|подтверждаю|подтверди|давай|го|запускай|покажи\s+карточку)[.!\s]*$", re.I)
# Последние карточки по change_id: «да» после цепочки показывает открытую карточку кнопкой.
_CARDS: dict[str, dict[str, Any]] = {}


def _remember(card: dict[str, Any]) -> None:
    _CARDS[str(card.get("change_id"))] = card
    while len(_CARDS) > 200:
        _CARDS.pop(next(iter(_CARDS)))


def open_card(session_id: str) -> dict[str, Any] | None:
    """Карточка, которую задача сессии ждёт сейчас (если окно её видело)."""
    from app.orchestrator.agents.runtime import get_runtime

    try:
        sup = get_runtime().supervisor
    except Exception:  # noqa: BLE001
        return None
    task_id = sup.active_task(session_id)
    change_id = sup._appr_open.get(task_id) if task_id is not None else None
    return _CARDS.get(str(change_id)) if change_id else None


def enabled() -> bool:
    return os.environ.get("AI_AGENT_ORCHESTRATOR", "1").strip().lower() not in {"0", "false", "no", "off"}


def session_for(project_id: int | None) -> str:
    """Та же сессия, что у чата оркестратора в студии: история и карточки общие."""
    from app.services.orch_tenancy import scoped_session

    public = f"studio-p{int(project_id)}" if project_id else "studio-factory"
    return scoped_session(public)


# Своё у окна «ИИ-агент»: правка кода и git (под админом), его кнопки брифинга и списка действий.
_LEGACY_RE = re.compile(
    r"(?<![а-я])(?:в\s+коде|код[ае]?\s+(?:студии|программы|приложения)|исходник|git|гит|коммит|"
    r"запушь|пушни|push|pull\s*request|пулл?\s*реквест|\.py|\.tsx?)"
    r"|^брифинг проекта$|^покажи доступные действия по проекту$",
    re.I,
)


def clean_text(message: str) -> tuple[str, bool]:
    """Текст для оркестратора и флаг «можно ему отдавать». Вложения, код, брифинг — прежнему пути."""
    text = _AUTO_PREFIX_RE.sub("", message or "").strip()
    if "[Вложенные файлы:" in text or not text or _LEGACY_RE.search(text):
        return text, False
    return text, True


def _text_of(payload: dict[str, Any]) -> str:
    for key in ("text", "text_ru", "message", "summary"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


_RISK_RU = {"none": "нет", "low": "низкий", "medium": "средний", "high": "высокий", "critical": "критичный"}


def card_title(card: dict[str, Any]) -> str:
    """Строка карточки окна: что сделает, по шагам, цена и риск."""
    parts = [str(card.get("title") or "Действие оркестратора").strip()]
    steps = [s for s in card.get("steps") or [] if isinstance(s, dict)]
    if len(steps) > 1 or (steps and steps[0].get("params_text")):
        lines = []
        for step in steps[:8]:
            text = str(step.get("text") or step.get("title") or "").strip()
            if text:
                lines.append(text + (" (платно)" if step.get("paid") else ""))
        if lines:
            parts.append("шаги: " + "; ".join(lines))
    if card.get("cost_text"):
        parts.append(str(card["cost_text"]))
    risk = card.get("plan_risk") or card.get("risk")
    if risk:
        parts.append("риск: " + _RISK_RU.get(str(risk), str(risk)))
    return " · ".join(parts)


def _card_item(card: dict[str, Any]) -> dict[str, Any]:
    title = card_title(card)
    return {
        "kind": "action",
        "token": TOKEN_PREFIX + str(card.get("change_id") or ""),
        "title": title,
        "message": title,
        "count": max(1, len(card.get("steps") or [])),
        "nodes": [],
        "source": "orchestrator",
        "actions": [str(s.get("action")) for s in card.get("steps") or [] if isinstance(s, dict) and s.get("action")]
        or ([str(card["action"])] if card.get("action") else []),
    }


async def _collect(queue: asyncio.Queue, after_seq: int, timeout: float) -> dict[str, Any]:
    """События сессии после ``after_seq``: последняя реплика, карточка, итог задачи."""
    reply, card, status, error = "", None, None, None
    phases: list[str] = []
    started = time.monotonic()
    last_reply_at: float | None = None
    while True:
        left = timeout - (time.monotonic() - started)
        if left <= 0:
            status = status or "timeout"
            break
        if last_reply_at is not None and time.monotonic() - last_reply_at > _IDLE_AFTER_REPLY_S:
            break
        try:
            event = await asyncio.wait_for(queue.get(), timeout=min(1.0, left))
        except TimeoutError:
            continue
        if int(getattr(event, "seq", 0) or 0) <= after_seq:
            continue
        typ = str(getattr(event, "type", "") or "")
        payload = dict(getattr(event, "payload", None) or {})
        if typ in ("approval_required", "approval") and payload.get("change_id"):
            card = payload
            _remember(card)
            break
        if typ == "message" and str(payload.get("role") or "assistant") != "user":
            text = _text_of(payload)
            if text:
                reply = text
                last_reply_at = time.monotonic()
        elif typ == "agent_status":
            text = _text_of(payload)
            if text:
                phases.append(text)
        elif typ in _END_TYPES:
            status = typ
            error = payload.get("error") or payload.get("reason")
            if reply:
                break
            last_reply_at = time.monotonic()
    return {"reply": reply, "card": card, "status": status, "error": error, "phases": phases}


async def _turn(session_id: str, text: str, project_id: int | None, app: Any) -> dict[str, Any] | None:
    from app.orchestrator.agents.runtime import get_runtime

    try:
        runtime = get_runtime()
    except Exception:  # noqa: BLE001 — движок не поднят (ENGINE_STARTUP=0): прежний путь окна
        return None
    async with runtime.bus.subscription(session_id) as queue:
        ack = await runtime.queue.accept(session_id, text, None, project_id=project_id, app=app)
        return await _collect(queue, int(ack or 0), TURN_TIMEOUT_S)


def handled(result: dict[str, Any] | None) -> bool:
    if not result:
        return False
    if result.get("card"):
        return True
    reply = str(result.get("reply") or "")
    return bool(reply) and not _NOT_HANDLED_RE.search(reply)


def done_payload(result: dict[str, Any]) -> dict[str, Any]:
    """Ответ в формате окна «ИИ-агент» (``DbOrchestratorChatResult``)."""
    card = result.get("card")
    reply = str(result.get("reply") or "")
    pending = [_card_item(card)] if card else []
    if card and not reply:
        steps = [str(x.get("text") or x.get("title") or "").strip() for x in card.get("steps") or [] if isinstance(x, dict)]
        what = "; ".join(t for t in steps if t) or str(card.get("title") or "")
        reply = (f"Сделаю: {what}." if what else "") + " Это нужно подтвердить — кнопка ниже."
        reply = reply.strip()
    return {
        "reply": reply,
        "applied": None,
        "actions_run": [],
        "ui_actions": [],
        "pending_confirm": pending,
        "choices": [],
        "error": None,
        "engine": "orchestrator",
    }


async def stream_turn(project_id: int | None, message: str, app: Any) -> AsyncIterator[tuple[str, Any]]:
    """События для SSE окна: ``("phase", текст)`` по ходу и ``("result", dict|None)`` в конце.

    ``None`` в конце — оркестратор не взялся, окно идёт прежним путём.
    """
    text, ok = clean_text(message)
    if not (enabled() and ok):
        yield "result", None
        return
    session_id = session_for(project_id)
    waiting = open_card(session_id) if _YES_RE.match(text) else None
    if waiting is not None:
        yield "result", done_payload({"reply": "Подтвердите следующий шаг:", "card": waiting})
        return
    task = asyncio.create_task(_turn(session_id, text, project_id, app))
    yield "phase", "Оркестратор разбирает просьбу…"
    try:
        while not task.done():
            await asyncio.sleep(0.5)
        result = task.result()
    except Exception as exc:  # noqa: BLE001
        logger.warning("ai-agent bridge: orchestrator failed, legacy path: {!r}", exc)
        yield "result", None
        return
    finally:
        if not task.done():
            task.cancel()
    good = handled(result)
    logger.info(
        "ai-agent bridge project={} handled={} card={} status={}",
        project_id,
        good,
        bool(result and result.get("card")),
        result and result.get("status"),
    )
    yield "result", (done_payload(result) if good and result else None)


async def decide(request: Any, project_id: int | None, token: str, approve: bool) -> dict[str, Any]:
    """Кнопка карточки окна для карточки оркестратора: одобрить или отказать и дождаться итога."""
    from app.orchestrator.agents.runtime import get_runtime
    from app.services import orch_bridge

    change_id = token[len(TOKEN_PREFIX):]
    session_id = session_for(project_id)
    runtime = get_runtime()
    if not approve:
        try:
            result = await runtime.supervisor.reject_approval(session_id, change_id=change_id)
        except Exception as exc:  # noqa: BLE001
            logger.info("ai-agent bridge reject {}: {!r}", change_id, exc)
            return {"ok": True, "reply": "Отменено — ничего не менял.", "notes": []}
        decided = result.get("change_id") if isinstance(result, dict) else None
        plan_id = await orch_bridge.bound_plan_any(decided) if decided else None
        if plan_id:
            await orch_bridge.revoke(plan_id, decided, orch_bridge.actor_of(dict(request.scope)))
        return {"ok": True, "reply": "Отменено — ничего не менял.", "notes": []}

    from app.web.routers.orchestrator_chat import DECISION_ERRORS, human_decision

    async with runtime.bus.subscription(session_id) as queue:
        try:
            await human_decision(request, session_id=session_id, change_id=change_id, decision="approve", retry_grant=False)
        except DECISION_ERRORS as exc:
            detail = getattr(exc, "detail", None) or str(exc)
            if getattr(exc, "status", None) == 403 or "operator" in str(detail):
                reply = "Подтвердить может только оператор студии: войдите в студию и нажмите ещё раз."
            else:
                reply = "Эта карточка устарела — повторите просьбу в чате."
            return {"ok": False, "reply": reply, "notes": [], "error": str(detail)[:200]}
        # Подписка живая: всё, что пришло после неё, — итог этого решения.
        result = await _collect(queue, 0, DECIDE_TIMEOUT_S)
    reply = str(result.get("reply") or "")
    pending: list[dict[str, Any]] = []
    if result.get("card"):
        _remember(result["card"])
        pending = [_card_item(result["card"])]
        reply = (reply + "\n\n" if reply else "") + "Дальше нужно ещё одно подтверждение — кнопка ниже."
    if not reply:
        reply = "Принято, выполняю. Итог придёт в чат." if result.get("status") in (None, "timeout") else "Готово."
    ok = result.get("status") != "task_failed"
    return {
        "ok": ok,
        "reply": reply,
        "notes": [],
        "error": None if ok else (result.get("error") or "task_failed"),
        "pending_confirm": pending,
    }
