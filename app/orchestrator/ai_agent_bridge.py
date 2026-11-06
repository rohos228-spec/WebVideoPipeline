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
# Только начало ответа: в разборе сценария про сов «не умеют» стояло в середине, и мост
# отдавал готовый ответ старому пути.
_NOT_HANDLED_RE = re.compile(
    r"^\s*(?:Не понял, что сделать|Этого чат пока не умеет\.?\s*$|Я не умею|Модель сейчас не отвечает)", re.I
)
_AUTO_PREFIX_RE = re.compile(r"^\[РЕЖИМ:[^\]]*\]\s*", re.S)
_END_TYPES = {"task_done", "task_failed"}
# Сессия -> seq просьбы, чей итог не успел к ответу окна.
_LATE: dict[str, int] = {}
STILL_WORKING_TEXT = (
    "Просьба ещё выполняется. Итог покажу со следующим сообщением, можно спросить «что с задачей»."
)
_NO_RE = re.compile(
    r"^(?:нет|не\s+надо|не\s+нужно|отмена|отмени|отменить|отбой|передумал[аи]?|не\s+делай)"
    r"(?:[,\s]+(?:не\s+надо|не\s+нужно|отмена|спасибо|передумал[аи]?))*[.!\s]*$",
    re.I,
)
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


async def waiting_card(session_id: str) -> dict[str, Any] | None:
    """Открытая карточка сессии, в том числе пережившая перезапуск сервера.

    Движок поднимает такую задачу из базы, а ``_CARDS`` живёт в памяти: окно не знало о карточке,
    и каждая новая просьба вставала за ней в очередь на 4 минуты.
    """
    from app.orchestrator.agents.runtime import get_runtime

    try:
        runtime = get_runtime()
    except Exception:  # noqa: BLE001 — движок не поднят: прежний путь окна
        return None
    sup = runtime.supervisor
    try:
        await sup.adopt_after_restart(session_id)
        task_id = sup.active_task(session_id)
        row = sup.tasks.get(task_id) if task_id is not None else None
        if not row or str(row.get("status") or "") != "waiting_approval":
            return None
        change_id = sup._appr_open.get(task_id)
        known = _CARDS.get(str(change_id)) if change_id else None
        if known is not None:
            return known
        for event in reversed(await runtime.bus.replay_from(session_id, 0)):
            payload = dict(getattr(event, "payload", None) or {})
            if getattr(event, "type", "") == "approval_required" and getattr(event, "task_id", None) == task_id:
                if payload.get("change_id"):
                    _remember(payload)
                    return payload
        return {"change_id": change_id or "", "title": str(row.get("goal") or "прошлая просьба")}
    except Exception as exc:  # noqa: BLE001 — без карточки окно работает как раньше
        logger.info("ai-agent bridge waiting card {}: {!r}", session_id, exc)
        return None


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


_UUID_RE = re.compile(r"\b(?:parent_uuid|uuid|cell_id)\s+[0-9a-f]{12,}\s*;?\s*", re.I)
_PROJECT_TAIL_RE = re.compile(r";?\s*проект #(\d+)\s*$")


def _action_steps(card: dict[str, Any]) -> list[dict[str, Any]]:
    """Шаги, которые что-то меняют или стоят денег; чтения перед ними на карточке — шум."""
    steps = [s for s in card.get("steps") or [] if isinstance(s, dict)]
    acting = [s for s in steps if s.get("paid") or str(s.get("risk") or "none") != "none"]
    acting = acting or steps
    # Следующая карточка плана несёт весь план: сделанные шаги уже позади.
    current = next((i for i, s in enumerate(acting) if s.get("action") == card.get("action")), 0)
    return acting[current:]


def _step_lines(card: dict[str, Any]) -> tuple[list[str], str]:
    """Тексты шагов без служебных ключей; общий проект — один раз, а не у каждого шага."""
    lines: list[str] = []
    projects: set[str] = set()
    for step in _action_steps(card):
        text = _UUID_RE.sub("", str(step.get("text") or step.get("title") or "")).strip(" ;")
        text = re.sub(r"\bvoiceover\s+", "закадр: ", text)
        found = _PROJECT_TAIL_RE.search(text)
        if found:
            projects.add(found.group(1))
        lines.append(text)
    if len(projects) == 1:
        lines = [_PROJECT_TAIL_RE.sub("", line).strip(" ;") for line in lines]
    lines = [line.rstrip(" —-;") for line in lines]
    project = f"проект #{next(iter(projects))}" if len(projects) == 1 else ""
    return [line for line in lines if line], project


def card_title(card: dict[str, Any]) -> str:
    """Строка карточки окна: что сделает, по шагам, цена и риск."""
    lines, project = _step_lines(card)
    steps = _action_steps(card)
    shown = [
        line + (" (платно)" if step.get("paid") else "")
        for line, step in zip(lines[:8], steps[:8], strict=False)
    ]
    if len(shown) == 1:
        # Один шаг: его текст и есть суть карточки, заголовок повторял бы его же.
        head = shown[0]
    else:
        head = str(card.get("title") or "").strip() or (lines[0].split(" — ")[0] if lines else "Действие")
    parts = [head + (f" ({project})" if project else "")]
    if len(shown) > 1:
        parts.append("шаги: " + "; ".join(shown))
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


async def _late_reply(session_id: str) -> str:
    """Итог просьбы, которая не успела к прошлому ответу окна: покажем со следующим."""
    after = _LATE.get(session_id)
    if after is None:
        return ""
    from app.orchestrator.agents.runtime import get_runtime

    try:
        events = await get_runtime().bus.replay_from(session_id, after)
    except Exception:  # noqa: BLE001
        return ""
    text = ""
    for event in events:
        payload = dict(getattr(event, "payload", None) or {})
        if getattr(event, "type", "") == "message" and str(payload.get("role") or "assistant") != "user":
            text = _text_of(payload) or text
    if not text:
        return ""
    _LATE.pop(session_id, None)
    return "Итог прошлой просьбы: " + plain_errors(text)


async def _turn(session_id: str, text: str, project_id: int | None, app: Any) -> dict[str, Any] | None:
    from app.orchestrator.agents.runtime import get_runtime

    try:
        runtime = get_runtime()
    except Exception:  # noqa: BLE001 — движок не поднят (ENGINE_STARTUP=0): прежний путь окна
        return None
    async with runtime.bus.subscription(session_id) as queue:
        ack = await runtime.queue.accept(session_id, text, None, project_id=project_id, app=app)
        result = await _collect(queue, int(ack or 0), TURN_TIMEOUT_S)
        result["after_seq"] = int(ack or 0)
        return result


def handled(result: dict[str, Any] | None) -> bool:
    if not result:
        return False
    if result.get("card"):
        return True
    reply = str(result.get("reply") or "")
    return bool(reply) and not _NOT_HANDLED_RE.search(reply)


_RAW_ERROR_RE = re.compile(r"(?:шаг не выполнен:\s*)?ActionCallError:\s*HTTP \d+:\s*")
_ERROR_DICT_RE = re.compile(r"\{[^{}]*'message':\s*'([^']+)'[^{}]*\}")
_NO_ADVICE_RE = re.compile(r"\n?Что можно сделать: Устраните причину, указанную выше, и повторите\.?")


def plain_errors(text: str) -> str:
    """Отказ действия без кода ошибки: «оба слота пустые», а не «ActionCallError: HTTP 400: …»."""
    out = _RAW_ERROR_RE.sub("", text)
    out = _ERROR_DICT_RE.sub(lambda m: m.group(1), out)
    return _NO_ADVICE_RE.sub("", out).strip()


_CARD_WARNINGS = 3


def done_payload(result: dict[str, Any]) -> dict[str, Any]:
    """Ответ в формате окна «ИИ-агент» (``DbOrchestratorChatResult``)."""
    card = result.get("card")
    reply = plain_errors(str(result.get("reply") or ""))
    pending = [_card_item(card)] if card else []
    if card and not reply:
        lines, project = _step_lines(card)
        what = "; ".join(lines) or str(card.get("title") or "")
        if project and what:
            what += f" ({project})"
        reply = (f"Сделаю: {what}." if what else "") + " Это нужно подтвердить — кнопка ниже."
        reply = reply.strip()
    if card:
        # «Не поставлен в очередь: #6 — …»: без этой строки проект пропадал из ответа молча.
        warned = [plain_errors(str(w)) for w in card.get("warnings") or [] if str(w).strip() and str(w) not in reply]
        if warned:
            reply = "\n\n".join([reply, *warned[:_CARD_WARNINGS]]).strip()
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


def card_action(card: dict[str, Any]) -> str:
    """Что предлагала карточка, короткой фразой для ответа."""
    lines, _project = _step_lines(card)
    return "; ".join(lines[:3]) or str(card.get("title") or "действие карточки")


def _is_question(text: str) -> bool:
    from app.orchestrator.queue import is_plain_question, is_status_question

    return is_status_question(text) or is_plain_question(text)


async def drop_card(session_id: str, card: dict[str, Any]) -> None:
    """Снять открытую карточку словами человека: то же, что кнопка «Отмена»."""
    from app.orchestrator.agents.runtime import get_runtime
    from app.services import orch_bridge

    change_id = str(card.get("change_id") or "")
    try:
        result = await get_runtime().supervisor.reject_approval(session_id, change_id=change_id or None)
    except Exception as exc:  # noqa: BLE001 — карточка могла уже закрыться
        logger.info("ai-agent bridge drop card {}: {!r}", change_id, exc)
        return
    decided = result.get("change_id") if isinstance(result, dict) else None
    plan_id = await orch_bridge.bound_plan_any(decided) if decided else None
    if plan_id:
        await orch_bridge.revoke(plan_id, decided, "ai_agent_window")


async def stream_turn(project_id: int | None, message: str, app: Any) -> AsyncIterator[tuple[str, Any]]:
    """События для SSE окна: ``("phase", текст)`` по ходу и ``("result", dict|None)`` в конце.

    ``None`` в конце — оркестратор не взялся, окно идёт прежним путём.
    """
    text, ok = clean_text(message)
    if not (enabled() and ok):
        yield "result", None
        return
    session_id = session_for(project_id)
    waiting = await waiting_card(session_id)
    dropped = ""
    late = await _late_reply(session_id)
    if waiting is not None:
        if _YES_RE.match(text):
            yield "result", done_payload({"reply": "Подтвердите кнопкой ниже:", "card": waiting})
            return
        action = card_action(waiting)
        if _NO_RE.match(text):
            await drop_card(session_id, waiting)
            yield "result", done_payload({"reply": f"Отменил: {action}. Ничего не менял."})
            return
        if not _is_question(text):
            # Новая просьба при открытой карточке вставала за ней в очередь: окно молчало
            # 4 минуты и уходило в старый путь. Человек передумал — снимаем, ничего не сделано.
            await drop_card(session_id, waiting)
            dropped = f"Прошлую карточку сняла новая просьба, она не выполнялась: {action}."
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
    if result and result.get("status") == "timeout" and not result.get("card") and not result.get("reply"):
        # Движок ещё работает над просьбой: старый путь сделал бы её второй раз.
        _LATE[session_id] = int(result.get("after_seq") or 0)
        payload = done_payload({"reply": STILL_WORKING_TEXT})
        payload["reply"] = "\n\n".join(x for x in (late, dropped, payload["reply"]) if x)
        yield "result", payload
        return
    good = handled(result)
    logger.info(
        "ai-agent bridge project={} handled={} card={} status={}",
        project_id,
        good,
        bool(result and result.get("card")),
        result and result.get("status"),
    )
    payload = done_payload(result) if good and result else None
    if payload and (dropped or late):
        payload["reply"] = "\n\n".join(x for x in (late, dropped, payload["reply"]) if x)
    yield "result", payload


_AUTO_NEXT_LIMIT = 6


def same_plan_free_step(approved: dict[str, Any] | None, following: dict[str, Any]) -> bool:
    """Следующая карточка того же плана: бесплатный шаг не выше среднего риска, виден на прошлой."""
    if not approved or approved.get("task_id") != following.get("task_id"):
        return False
    if str(following.get("risk") or "") == "high" or str(following.get("plan_risk") or "") == "high":
        return False
    action = following.get("action")
    seen = [s for s in approved.get("steps") or [] if isinstance(s, dict) and s.get("action") == action]
    now = [s for s in following.get("steps") or [] if isinstance(s, dict) and s.get("action") == action]
    if not action or not seen or not now:
        return False
    return not any(s.get("paid") for s in [*seen, *now]) and approved.get("steps") == following.get("steps")


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
        # Шаги плана человек уже видел на карточке и подтвердил: бесплатный следующий не спрашиваем.
        approved = _CARDS.get(change_id)
        for _ in range(_AUTO_NEXT_LIMIT):
            following = result.get("card")
            if not following or not same_plan_free_step(approved, following):
                break
            logger.info("ai-agent bridge: next free step of approved plan {}", following.get("change_id"))
            try:
                await human_decision(
                    request,
                    session_id=session_id,
                    change_id=str(following.get("change_id")),
                    decision="approve",
                    retry_grant=False,
                )
            except DECISION_ERRORS as exc:
                logger.info("ai-agent bridge: next step not approved: {!r}", exc)
                break
            result = await _collect(queue, 0, DECIDE_TIMEOUT_S)
    reply = plain_errors(str(result.get("reply") or ""))
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
