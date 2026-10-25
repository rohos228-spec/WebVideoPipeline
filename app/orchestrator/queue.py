"""Очередь входящих сообщений.

Ack («принял, разбираю») уходит сразу, до классификации. Классификация —
сначала детерминированные правила, спорное отдаётся ``router.classify``.
Задача супервизора это не ждёт: разбор идёт отдельной asyncio-задачей.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

from loguru import logger
from sqlalchemy import text

from app.orchestrator.contracts import (
    ORCH_DEFAULT_TIER,
    ORCH_REASONING_EFFORT,
    AgentRole,
    BudgetScope,
    ClassifyResult,
    MessageMode,
    budget_exhausted_response,
    orch_call_tier,
    orch_run_id,
)
from app.orchestrator.db.migrations import connect, utc_now
from app.orchestrator.events import EventBus
from app.orchestrator.safety_gate import Refusal, refusal_for
from app.orchestrator.small_talk import CARD_WAITING_TEXT, OK_TEXT, THANKS_TEXT, is_decline, small_talk_reply

_TOKEN_RE = re.compile(r"[a-zа-яё0-9]+", re.IGNORECASE)
_STOP_TOKENS = frozenset(
    {
        "стой",
        "отмена",
        "останови",
        "остановись",
        "стоп",
        "прерви",
        "прекрати",
        "stop",
        "cancel",
    }
)
_STOP_OBJECTS = ("проект", "генерац", "очеред", "шаг", "картин", "видео", "озвуч", "нод")
# «отмени последнее действие», «отмена правки» — откат правки, а не стоп задачи чата.
_UNDO_RE = re.compile(
    r"(?<![а-яa-z])(?:отмени(?:те)?|отменить|отмена)\s+(?:[а-я]+\s+){0,2}?"
    r"(?:последн|правк|изменени|действи|смен|настройк)[а-я]*"
    r"|(?<![а-яa-z])undo(?![a-z])"
)
# "отмени последнюю задачу", "отмена последнего запуска" — это стоп задачи, а не откат правки.
_CANCEL_TASK_RE = re.compile(
    r"(?<![а-яa-z])(?:отмени(?:те)?|отменить|отмена)\s+(?:[а-я]+\s+){0,2}?(?:задач|задани|запуск)"
)
_UNDO_EXCLUDED = ("задач", "задани", "запуск", "генерац", "карточк")
_KILL_PHRASES = (
    "аварийный стоп",
    "kill switch",
    "kill-switch",
)
_STEER_PHRASES = (
    "измени план",
    "поменяй план",
    "перепланируй",
    "скорректируй план",
    "вместо этого",
    "учти что",
    "steer:",
)
_QUESTION_STARTS = frozenset(
    {
        "что",
        "как",
        "почему",
        "зачем",
        "когда",
        "где",
        "кто",
        "какой",
        "какая",
        "какие",
        "какое",
        "сколько",
        "можно",
        "нужно",
    }
)
_TASK_MARKERS = ("задач", "план", "шаг", "нод", "кадр", "сцен")
# «Открой муравьев»: открыть вкладку чат не может, ответ подсказывает, где проект слева.
_OPEN_STARTS = frozenset({"открой", "откройте", "открыть"})
_ASK_STARTS = _QUESTION_STARTS - {"можно", "нужно"}
_IMPERATIVE_WORDS = (
    "поставь",
    "ставь",
    "установи",
    "смени",
    "поменяй",
    "замени",
    "переключи",
    "назначь",
    "запусти",
    "перезапусти",
    "стартуй",
    "продолжи",
    "повтори",
    "сделай",
    "переделай",
    "создай",
    "добавь",
    "убери",
    "удали",
    "включи",
    "выключи",
    "отключи",
    "верни",
    "откати",
    "исправь",
    "почини",
    "перегенерируй",
    "сгенерируй",
    "сформируй",
    "подготовь",
    "собери",
    "выбери",
    "обнови",
    "примени",
    "перенеси",
    "переименуй",
    "скопируй",
    "сохрани",
    "выгрузи",
    "загрузи",
    "настрой",
    "снизь",
    "понизь",
    "уменьши",
    "увеличь",
    "подними",
    "отредактируй",
    "перепиши",
    "пропусти",
    "подтверди",
    "одобри",
    "отклони",
    "допиши",
    "дополни",
    "пусть",
)
_STATUS_PHRASES = (
    "статус",
    "как дела",
    "как там",
    "как идет",
    "что с ",
    "что там",
    "что сейчас",
    "что происходит",
    "где сейчас",
    "на каком",
    "прогресс",
    "сколько потрат",
    "сколько потрач",
    "сколько осталось",
    "готов ли",
    "готово ли",
    "готовы ли",
    "идет ли",
    "закончил",
    "закончилась",
    "доделал",
)
# Глаголы запуска, которых нет в _IMPERATIVE_WORDS: «прогони», «пусти», «запускай».
_RUN_VERBS_RE = re.compile(r"(?<![а-я])(?:прогони|прогоняй|пусти|запускай|гони|продолжай)(?:те)?(?![а-я])")
_IMPERATIVES = frozenset(_IMPERATIVE_WORDS + tuple(word + "те" for word in _IMPERATIVE_WORDS))
_NEGATIONS = frozenset({"не", "ни"})


def has_imperative(text: str) -> bool:
    tokens = _TOKEN_RE.findall(" ".join((text or "").lower().replace("ё", "е").split()))
    return any(
        token in _IMPERATIVES and (index == 0 or tokens[index - 1] not in _NEGATIONS)
        for index, token in enumerate(tokens)
    )


def is_plain_question(text: str) -> bool:
    """«Какая модель стоит на сценарии?»: вопрос без просьбы. Пока ждёт карточка, отвечается сразу."""
    norm = " ".join((text or "").lower().replace("ё", "е").split())
    if not norm or has_imperative(norm) or _RUN_VERBS_RE.search(norm):
        return False
    tokens = _TOKEN_RE.findall(norm)
    return norm.endswith("?") or (bool(tokens) and tokens[0] in _ASK_STARTS)


def is_status_question(text: str) -> bool:
    """Вопрос о ходе дел без просьбы что-то сделать: отвечается сразу, задачей не ставится."""
    norm = " ".join((text or "").lower().replace("ё", "е").split()) + " "
    if has_imperative(norm) or _RUN_VERBS_RE.search(norm):
        # «прогони дальше, что с лимитом?» - просьба с вопросом, это задача.
        return False
    return any(phrase in norm for phrase in _STATUS_PHRASES)


def _undo_request(text: str) -> bool:
    from app.services.action_layer.chat_undo import undo_phrase

    return undo_phrase(text)


def cancel_or_undo(text: str) -> bool:
    """«Отмени последнее» при живой задаче: остановить её или вернуть правку — решает классификатор."""
    norm = " ".join((text or "").lower().split())
    tokens = _TOKEN_RE.findall(norm)
    if any(token in _STOP_TOKENS for token in tokens):
        return False
    return any(token.startswith("отмен") for token in tokens) and _undo_request(norm)


def classify_deterministic(text: str) -> ClassifyResult | None:
    """Правила до LLM. None — спорный случай, нужен fast-классификатор."""
    norm = " ".join((text or "").lower().split())
    tokens = _TOKEN_RE.findall(norm)
    if any(phrase in norm for phrase in _KILL_PHRASES):
        return ClassifyResult(
            tier="fast",
            mode=MessageMode.interrupt,
            deterministic=True,
            confidence=1.0,
            kill_switch=True,
        )
    plain = norm.replace("ё", "е")
    # «стоп, отмени последнее»: явное стоп-слово важнее отката («отмена» само по себе не в счёт).
    stop_word = any(token in _STOP_TOKENS and token != "отмена" for token in tokens)
    if _CANCEL_TASK_RE.search(plain) and not any(obj in norm for obj in _STOP_OBJECTS):
        return ClassifyResult(
            tier="fast",
            mode=MessageMode.interrupt,
            deterministic=True,
            confidence=1.0,
        )
    # Откат — только если и разбор отката согласен: вопрос "как отменить…?" и "отмени это действие" — нет.
    if (
        not stop_word
        and not any(obj in plain for obj in _UNDO_EXCLUDED)
        and _UNDO_RE.search(plain)
        and _undo_request(plain)
    ):
        return ClassifyResult(
            tier="strong",
            mode=MessageMode.task,
            deterministic=True,
            confidence=1.0,
        )
    if any(token in _STOP_TOKENS for token in tokens) and any(obj in norm for obj in _STOP_OBJECTS):
        # «Останови все проекты» — остановить генерацию в Студии, а не только задачу чата.
        return ClassifyResult(
            tier="strong",
            mode=MessageMode.task,
            deterministic=True,
            confidence=1.0,
        )
    steer = any(phrase in norm for phrase in _STEER_PHRASES)
    if not steer and not any(token in _STOP_TOKENS for token in tokens) and _undo_request(norm):
        # «Отмени последнее» — вернуть свою правку, а не прервать задачу.
        return ClassifyResult(
            tier="strong",
            mode=MessageMode.task,
            deterministic=True,
            confidence=1.0,
        )
    if any(token in _STOP_TOKENS for token in tokens):
        return ClassifyResult(
            tier="fast",
            mode=MessageMode.interrupt,
            deterministic=True,
            confidence=1.0,
        )
    if steer:
        return ClassifyResult(
            tier="strong",
            mode=MessageMode.steer,
            deterministic=True,
            confidence=1.0,
        )
    if has_imperative(norm):
        return ClassifyResult(
            tier="strong",
            mode=MessageMode.task,
            deterministic=True,
            confidence=1.0,
        )
    # «Сценарий» — имя ноды, а не «сцена»: вопрос про модель на сценарии остаётся вопросом.
    mentions_task = any(marker in norm.replace("сценари", "") for marker in _TASK_MARKERS)
    is_question = norm.endswith("?") or (bool(tokens) and tokens[0] in _QUESTION_STARTS)
    asks = norm.endswith("?") and bool(tokens) and tokens[0] in _ASK_STARTS
    if asks or (is_question and not mentions_task) or (bool(tokens) and tokens[0] in _OPEN_STARTS):
        return ClassifyResult(
            tier="fast",
            mode=MessageMode.append,
            deterministic=True,
            confidence=1.0,
        )
    return None


# «Останови проект 7 и запусти 8»: смешанная просьба идёт обычной задачей.
_STOP_VERB = r"(?:останови(?:те)?|стоп|прекрати(?:те)?|прерви(?:те)?)"
_PROJECT_REF = r"(?:№\s*|#)?\d{1,9}"
_PROJECT_LIST = _PROJECT_REF + r"(?:\s*(?:,|и|,\s*и)\s*" + _PROJECT_REF + r")*"
# Вся реплика — только «стоп-глагол + номера проектов». Всё прочее («кроме», «не трогай»,
# «стоп-кадр», вопрос, второе действие, «через 10 минут») идёт обычной задачей.
_TARGETED_STOP_RE = re.compile(
    r"(?:пожалуйста\s*,?\s*)?"
    + _STOP_VERB
    + r"\s+(?:(?:проект|проекты|проекта|проектов)\s+"
    + _PROJECT_LIST
    + r"|(?:№\s*|#)\d{1,9}(?:\s*(?:,|и|,\s*и)\s*(?:№\s*|#)\d{1,9})*)"
    + r"(?:\s*,?\s*пожалуйста)?[.!]*"
)


_TARGETED_STOP_MAX = 10


def targeted_stop_projects(text: str) -> list[int] | None:
    """«Останови проект 7», «стоп проекты 7 и 9», «останови #7, #8»: номера, иначе None.

    Только когда реплика целиком — стоп названных проектов. Пока в чате идёт задача,
    такая реплика не встаёт в очередь за ней: там она дождалась бы конца всех генераций.
    """
    norm = " ".join((text or "").lower().split()).replace("ё", "е")
    if not _TARGETED_STOP_RE.fullmatch(norm):
        return None
    found: list[int] = []
    for raw in re.findall(r"\d+", norm):
        value = int(raw)
        if value <= 0:
            return None
        if value not in found:
            found.append(value)
    if len(found) > _TARGETED_STOP_MAX:
        # Длинный список — обычной задачей, а не молча первые десять.
        return None
    return found or None


def _stop_outcome(result: Any) -> tuple[bool, str]:
    if result is None:
        return False, ""
    if isinstance(result, dict):
        ok = bool(result.get("ok")) if "ok" in result else result.get("status") == "ok"
        error = result.get("error")
    else:
        ok = bool(getattr(result, "ok", False))
        data = getattr(result, "data", None)
        error = data.get("error") if isinstance(data, dict) else None
    return ok, str(error or "").strip()


class MessageQueue:
    def __init__(self, bus: EventBus, router: Any, supervisor: Any, context: Any | None = None) -> None:
        self.bus = bus
        self.router = router
        self.supervisor = supervisor
        self.context = context
        self.app: Any = None
        self._pending: set[asyncio.Task[Any]] = set()

    async def accept(
        self,
        session_id: str,
        text: str,
        mode: MessageMode | None = None,
        project_id: int | None = None,
        *,
        kill_switch: bool = False,
        app: Any = None,
    ) -> int:
        """Записать реплику, опубликовать ack и поставить разбор в фон.

        ``run_id`` резервируется здесь, до ``classify`` и ``call``.
        Явный ``mode`` не классифицируется, кроме фразы kill switch
        и поля ``kill_switch``: оба перебивают mode. Неизвестное значение
        mode отсекает HTTP. Возвращает seq события ack. Не ждёт разбор.
        """
        if app is not None:
            self.app = app
        task_id = self.supervisor.active_task(session_id)
        run_id = orch_run_id(task_id=task_id, session_id=session_id)
        message_id = await asyncio.to_thread(
            _insert_message,
            session_id,
            "user",
            text,
            task_id=task_id,
            mode=None,
        )
        ack = await self.bus.publish(
            session_id,
            "ack",
            {"text": text, "accepted": True},
            task_id=task_id,
        )
        job = asyncio.create_task(
            self._classify(
                session_id,
                text,
                run_id,
                mode,
                project_id,
                message_id,
                kill_switch=kill_switch,
            ),
            name=f"orch-classify-{session_id}",
        )
        self._pending.add(job)
        job.add_done_callback(self._pending.discard)
        return ack.seq

    async def drain(self, timeout: float = 2.0) -> None:
        """Дождаться разборов, которые уже стоят в фоне."""
        pending = [job for job in list(self._pending) if not job.done()]
        if not pending:
            return
        await asyncio.wait_for(asyncio.gather(*pending), timeout)

    async def _classify(
        self,
        session_id: str,
        text: str,
        run_id: str,
        mode: MessageMode | None,
        project_id: int | None = None,
        message_id: int | None = None,
        *,
        kill_switch: bool = False,
    ) -> None:
        from app.orchestrator.routing.classifier import is_control_request
        from app.services import spend_guard

        with spend_guard.chat_control(kill_switch or is_control_request(text)):
            await self._classify_turn(
                session_id, text, run_id, mode, project_id, message_id, kill_switch=kill_switch
            )

    async def _classify_turn(
        self,
        session_id: str,
        text: str,
        run_id: str,
        mode: MessageMode | None,
        project_id: int | None,
        message_id: int | None,
        *,
        kill_switch: bool,
    ) -> None:
        published = False
        try:
            if not kill_switch and mode is not MessageMode.interrupt:
                refusal = refusal_for(text)
                if refusal is not None:
                    published = True
                    await self._refuse(session_id, refusal, message_id)
                    return
            if not kill_switch and mode in (None, MessageMode.task):
                # Явный append (API) остаётся вопросом к модели: готовый ответ только для реплик из чата.
                reply = small_talk_reply(text)
                # «Спасибо, не надо» при открытой карточке — не отказ: кнопка «Отклонить» на карточке.
                if (reply == OK_TEXT or (reply == THANKS_TEXT and is_decline(text))) and self._card_waiting(
                    session_id
                ):
                    reply = CARD_WAITING_TEXT
                if reply is not None:
                    published = True
                    await self._small_talk(session_id, reply, message_id)
                    return
            forced = classify_deterministic(text)
            phrase = forced is not None and forced.kill_switch
            if kill_switch or phrase:
                decided = ClassifyResult(
                    tier="fast",
                    mode=MessageMode.interrupt,
                    deterministic=True,
                    confidence=1.0,
                    kill_switch=True,
                )
            elif mode is not None:
                decided = ClassifyResult(
                    tier=ORCH_DEFAULT_TIER,
                    mode=mode,
                    deterministic=True,
                    confidence=1.0,
                )
            else:
                decided = forced
                task_id = self.supervisor.active_task(session_id)
                if decided is not None and task_id is not None and cancel_or_undo(text):
                    # Идёт генерация или ждёт карточка: просьба со словом «отмени» может значить «останови».
                    decided = None
                if (
                    decided is None
                    and task_id is not None
                    and (is_status_question(text) or is_plain_question(text))
                ):
                    # Пока идёт задача, вопрос «как там проект» иначе встал бы в очередь за ней.
                    decided = ClassifyResult(
                        tier="fast",
                        mode=MessageMode.append,
                        deterministic=True,
                        confidence=1.0,
                    )
                if decided is None:
                    decided = await self.router.classify(
                        text,
                        run_id=run_id,
                        task_id=None if task_id is None else str(task_id),
                    )
                    decided = decided.model_copy(update={"deterministic": False})
                    if decided.mode is MessageMode.steer and task_id is None:
                        # Уточнять нечего: прошлая задача закончена — это новая просьба.
                        decided = decided.model_copy(update={"mode": MessageMode.task, "tier": "strong"})
            await self.bus.publish(
                session_id,
                "classification",
                {
                    "mode": decided.mode.value,
                    "tier": decided.tier,
                    "deterministic": decided.deterministic,
                    "confidence": decided.confidence,
                    "kill_switch": decided.kill_switch,
                },
            )
            published = True
            if message_id is not None:
                await asyncio.to_thread(_set_message_mode, message_id, decided.mode.value)
            await self._apply(session_id, text, decided, project_id)
        except Exception as exc:
            logger.exception("orch classification failed for session {}", session_id)
            if not published:
                if message_id is not None:
                    await asyncio.to_thread(_set_message_mode, message_id, MessageMode.append.value)
                await self.bus.publish(
                    session_id,
                    "classification",
                    {"mode": MessageMode.append.value, "deterministic": False, "error": "classifier_failed"},
                )
            try:
                await self.explain_failure(session_id, exc)
            except Exception:
                logger.exception("orch failure message not sent for session {}", session_id)

    async def _refuse(self, session_id: str, refusal: Refusal, message_id: int | None) -> None:
        await self.bus.publish(
            session_id,
            "classification",
            {
                "mode": MessageMode.append.value,
                "tier": "fast",
                "deterministic": True,
                "confidence": 1.0,
                "kill_switch": False,
                "refused": True,
            },
        )
        if message_id is not None:
            await asyncio.to_thread(_set_message_mode, message_id, MessageMode.append.value)
        await asyncio.to_thread(
            _insert_message,
            session_id,
            "assistant",
            refusal.text,
            task_id=None,
            mode=MessageMode.append.value,
        )
        await self.bus.publish(
            session_id,
            "message",
            {"role": "assistant", "text": refusal.text, "kind": "refusal", "codes": list(refusal.codes)},
        )

    async def _small_talk(self, session_id: str, text: str, message_id: int | None) -> None:
        """«привет», «спасибо», «помоги»: ответ без задачи, планировщика и модели."""
        await self.bus.publish(
            session_id,
            "classification",
            {
                "mode": MessageMode.append.value,
                "tier": "fast",
                "deterministic": True,
                "confidence": 1.0,
                "kill_switch": False,
            },
        )
        if message_id is not None:
            await asyncio.to_thread(_set_message_mode, message_id, MessageMode.append.value)
        await self._reply(session_id, text, "small_talk")

    def _card_waiting(self, session_id: str) -> bool:
        row = self._active_row(session_id)[1]
        return str(row.get("status") or "") == "waiting_approval"

    def _active_row(self, session_id: str) -> tuple[int | None, dict[str, Any]]:
        task_id = self.supervisor.active_task(session_id)
        tasks = getattr(self.supervisor, "tasks", None)
        row = (tasks.get(task_id) if tasks is not None and task_id is not None else None) or {}
        return task_id, row

    async def _reply(self, session_id: str, text: str, kind: str) -> None:
        await asyncio.to_thread(
            _insert_message,
            session_id,
            "assistant",
            text,
            task_id=None,
            mode=MessageMode.append.value,
        )
        await self.bus.publish(session_id, "message", {"role": "assistant", "text": text, "kind": kind})

    async def _interrupt(self, session_id: str) -> None:
        """Стоп без объекта: отменить задачу чата и всегда сказать, что остановлено."""
        task_id, row = self._active_row(session_id)
        status = str(row.get("status") or "")
        closes = getattr(self.supervisor, "stop_closes_card", None)
        if task_id is not None and status == "waiting_approval" and callable(closes) and closes(task_id):
            # Карточка после неизвестного исхода платного шага: стоп сам её закрывает, кнопки уже не будет.
            status = "cancelling"
        self.supervisor.request_cancel(session_id)
        await self._reply(
            session_id, stop_reply(str(row.get("goal") or ""), status, task_id is not None), "stop"
        )

    async def _apply(
        self,
        session_id: str,
        text: str,
        decided: ClassifyResult,
        project_id: int | None = None,
    ) -> None:
        if decided.kill_switch:
            idle = self.supervisor.active_task(session_id) is None
            job = self.supervisor.request_kill(session_id)
            payload = await job if job is not None else None
            body = payload if isinstance(payload, dict) else {}
            removed, stopped = body.get("queue_removed"), body.get("queue_stopped")
            note = queue_removed_note(
                removed if isinstance(removed, list) else [], stopped if isinstance(stopped, list) else []
            )
            if idle:
                # Без задачи итоговой сводки не будет: ответить сразу, иначе чат молчит.
                text = note or stop_reply("", "", False)
                await self._reply(session_id, text, "stop")
            elif note:
                await self._reply(session_id, note, "stop")
            return
        if decided.mode is MessageMode.interrupt:
            await self._interrupt(session_id)
            return
        if decided.mode is MessageMode.steer:
            if self.supervisor.active_task(session_id) is None:
                await self._reply(session_id, STEER_IDLE_TEXT, "steer_idle")
                return
            self.supervisor.request_steer(session_id, text)
            return
        if decided.mode is MessageMode.task:
            targets = targeted_stop_projects(text)
            if targets and self.supervisor.active_task(session_id) is not None:
                await self._stop_projects_now(session_id, targets)
                return
            await self.supervisor.submit_task(session_id, text, project_id=project_id)
            return
        self.supervisor.note_context(session_id, text)
        await self._stream_fast(session_id, text, decided.tier or "fast", project_id)

    async def _stop_projects_now(self, session_id: str, project_ids: list[int]) -> None:
        """Стоп названных проектов сразу, мимо очереди задач: задача чата и другие проекты идут дальше."""
        task_id = self.supervisor.active_task(session_id)
        actions = getattr(self.supervisor, "actions", None)
        app = self.app if self.app is not None else getattr(self.supervisor, "app", None)
        if app is None:
            from app.orchestrator import app_ref

            app = app_ref.current()
        lines: list[str] = []
        for project in project_ids:
            result: Any = None
            if actions is not None:
                ctx = {
                    "task_id": task_id,
                    "session_id": session_id,
                    "app": app,
                    "bus": self.bus,
                    "operator": f"orchestrator:task-{task_id}" if task_id is not None else "orchestrator",
                }
                try:
                    result = await actions.execute("project.stop", {"project_id": int(project)}, ctx)
                except Exception:
                    logger.exception("orch targeted stop of project {} failed", project)
            ok, error = _stop_outcome(result)
            if ok:
                lines.append(f"Проект #{project}: стоп отправлен.")
            else:
                lines.append(
                    f"Проект #{project} остановить не удалось" + (f": {error}" if error else "") + "."
                )
        lines.extend(self._stop_task_note(task_id, project_ids))
        await self._reply(session_id, " ".join(lines), "stop")

    def _stop_task_note(self, task_id: int | None, project_ids: list[int]) -> list[str]:
        """Что будет с задачей чата: соседи идут дальше, только этот проект или проект не из задачи."""
        if task_id is None:
            return []
        mine: set[int] = set()
        stored = getattr(self.supervisor, "_stored_plan", None)
        try:
            for step in stored(task_id) if callable(stored) else []:
                raw = (step.params or {}).get("project_id")
                if raw is not None and int(raw) > 0:
                    mine.add(int(raw))
        except Exception:
            logger.exception("orch targeted stop: plan of task {} not read", task_id)
        stopped = set(project_ids)
        outside = sorted(stopped - mine) if mine else []
        notes: list[str] = []
        if outside:
            listed = ", ".join(f"#{pid}" for pid in outside)
            notes.append(f"Задача чата {listed} не запускала, она идёт как шла.")
        rest = sorted(mine - stopped)
        if mine & stopped:
            if rest:
                notes.append(
                    "Остальные проекты задачи идут дальше: " + ", ".join(f"#{pid}" for pid in rest) + "."
                )
            else:
                notes.append("Других проектов у задачи нет: она закончится, когда шаг остановится.")
        return notes

    async def _question_messages(
        self,
        session_id: str,
        text: str,
        project_id: int | None,
    ) -> list[dict[str, Any]]:
        plain = [{"role": "user", "content": text}]
        if self.context is None:
            return plain
        try:
            if project_id is None:
                project_id = await asyncio.to_thread(self._session_project, session_id)
            app = self.app if self.app is not None else getattr(self.supervisor, "app", None)
            return await self.context.messages(text, project_id, app, session_id)
        except Exception:
            logger.exception("orch question context failed for session {}", session_id)
            return plain

    def _session_project(self, session_id: str) -> int | None:
        task_id = getattr(self.supervisor, "_session_task", {}).get(session_id)
        tasks = getattr(self.supervisor, "tasks", None)
        if task_id is None or tasks is None:
            return None
        raw = (tasks.get(task_id) or {}).get("project_id")
        try:
            value = int(raw) if raw is not None else 0
        except (TypeError, ValueError):
            return None
        return value if value > 0 else None

    async def _stream_fast(
        self,
        session_id: str,
        text: str,
        tier: str,
        project_id: int | None = None,
    ) -> None:
        """Заглушка стрима: слова из готового ``response.text``.

        Это не контракт UI. Боевой путь — keyword-only ``on_delta`` у
        ``router.call`` (WS5): каждый delta публикуется как token.
        Если роутер не зовёт ``on_delta``, остаётся нарезка готового текста.
        """
        task_id = self.supervisor.active_task(session_id)
        deltas: list[str] = []

        def on_delta(piece: str) -> None:
            deltas.append(str(piece))

        messages = await self._question_messages(session_id, text, project_id)
        try:
            response = await self.router.call(
                orch_call_tier(tier),
                messages,
                True,
                run_id=orch_run_id(task_id=task_id, session_id=session_id),
                task_id=None if task_id is None else str(task_id),
                agent_role=AgentRole.summarizer.value,
                budget_scope=BudgetScope.internal.value,
                reasoning_effort=ORCH_REASONING_EFFORT,
                on_delta=on_delta,
            )
        except Exception as exc:
            logger.warning("orch answer failed for session {}: {}", session_id, type(exc).__name__)
            fallback = _answer_fallback(text)
            if fallback is None:
                await self.explain_failure(session_id, exc, task_id)
            else:
                # Число известно без модели (предел параллели): ответить им, а не ошибкой.
                await self._reply(session_id, fallback, "fallback")
            return
        from app.orchestrator.agents.summarizer import normalize_links

        parts = deltas or _token_pieces(response.text)
        # Сохранённый и итоговый текст — со ссылками без пробела после «(»: иначе они видны как текст.
        answer = normalize_links(str(response.text or ""))
        if not answer.strip():
            # Пустую реплику не пишем и не показываем: пустой пузырь «оркестратор:» без слов.
            logger.warning("orch answer empty for session {}", session_id)
            answer = _answer_fallback(text) or (
                EMPTY_BUDGET_TEXT if budget_exhausted_response(response) else EMPTY_ANSWER_TEXT
            )
            parts = _token_pieces(answer)
        message_id = await asyncio.to_thread(
            _insert_message,
            session_id,
            "assistant",
            answer,
            task_id=task_id,
            mode=MessageMode.append.value,
        )
        await self.bus.publish(
            session_id,
            "message_start",
            {"message_id": message_id, "role": "assistant"},
            task_id=task_id,
        )
        for part in parts:
            await self.bus.publish(
                session_id,
                "token",
                {"text": part, "message_id": message_id},
                task_id=task_id,
            )
        await self.bus.publish(
            session_id,
            "message_end",
            {"message_id": message_id, "role": "assistant"},
            task_id=task_id,
        )
        if budget_exhausted_response(response) and task_id is not None:
            await self.supervisor.absorb_budget(task_id, session_id, response)
        elif budget_exhausted_response(response):
            await self.supervisor.publish_approval_required(
                session_id,
                {"reason": "budget_exhausted", "task_id": None},
                task_id=None,
            )
        await self.bus.publish(
            session_id,
            "message",
            {"role": "assistant", "text": answer},
            task_id=task_id,
        )

    async def explain_failure(self, session_id: str, exc: BaseException, task_id: int | None = None) -> None:
        from app.orchestrator.agents.summarizer import explain_failure

        text = "Ответить не получилось. " + explain_failure(exc)
        await asyncio.to_thread(
            _insert_message,
            session_id,
            "assistant",
            text,
            task_id=task_id,
            mode=MessageMode.append.value,
        )
        await self.bus.publish(
            session_id,
            "message",
            {"role": "assistant", "text": text, "kind": "error"},
            task_id=task_id,
        )


_PROJECT_STOP = "напишите «останови проект 5» с нужным номером"
_STOP_HINT = (
    "Генерацию в проектах это не останавливает: для неё нажмите кнопку «Стоп задачи» (в большом окне "
    "чата она называется «Остановить задачу» и останавливает задачу вместе с её проектом) или "
    + _PROJECT_STOP
    + "."
)
EMPTY_ANSWER_TEXT = "Не получилось ответить: модель вернула пустой ответ. Повторите вопрос."
EMPTY_BUDGET_TEXT = (
    "Не получилось ответить: закончился лимит трат на ответы чата. "
    "Подтвердите продолжение на карточке и повторите вопрос."
)


def _answer_fallback(text: str) -> str | None:
    """Ответ без модели, если он есть: предел параллели знает ``runtime_streams``."""
    from app.orchestrator.agents.question_context import parallel_limit_answer

    try:
        return parallel_limit_answer(text)
    except Exception:
        logger.exception("orch answer fallback failed")
        return None


STEER_IDLE_TEXT = (
    "Сейчас нет задачи, к которой это относится, поэтому я ничего не менял. "
    "Напишите просьбу целиком, например «запусти картинки в проекте 6»."
)


def queue_removed_note(project_ids: list[Any], stopped_ids: list[Any] | None = None) -> str:
    """Сколько проектов, поставленных чатом, аварийный стоп убрал из очереди генерации."""
    from app.services.action_layer.card_text import projects_ru

    def _ints(items: list[Any] | None) -> list[int]:
        return [int(item) for item in items or [] if isinstance(item, int) and not isinstance(item, bool)]

    ids = _ints(project_ids)
    if not ids:
        return ""
    stopped = [pid for pid in _ints(stopped_ids) if pid in ids]
    listed = ", ".join(f"#{pid}" for pid in ids)
    text = f"Из очереди генерации убрал {projects_ru(len(ids))}, которые ставил чат: {listed}. "
    if stopped:
        text += (
            f"У {', '.join(f'#{pid}' for pid in stopped)} шаг уже шёл - он останавливается, "
            "дальше эти проекты не пойдут. "
        )
    return text + "Сами они не стартуют, запустить снова можно кнопкой ▶ или просьбой в чате."


def stop_reply(goal: str, status: str, active: bool) -> str:
    """Ответ на «стоп» / «останови все»: что остановлено, а что нет."""
    name = " ".join(goal.split())
    name = f" «{name[:80]}»" if name else ""
    if not active:
        return (
            "Сейчас нечего останавливать: задач в чате нет. "
            "Чтобы остановить генерацию проекта, " + _PROJECT_STOP + "."
        )
    if status == "waiting_approval":
        return (
            f"Задача{name} ждёт вашего решения на карточке и сама ничего не запустит. "
            "Чтобы отказаться, нажмите «Отклонить». " + _STOP_HINT
        )
    return f"Останавливаю задачу чата{name}: новых шагов она не начнёт. " + _STOP_HINT


def _token_pieces(text: str) -> list[str]:
    if not text:
        return []
    bits = text.split(" ")
    pieces: list[str] = []
    for index, bit in enumerate(bits):
        pieces.append(bit if index == len(bits) - 1 else bit + " ")
    return pieces


def _insert_message(
    session_id: str,
    role: str,
    content: str,
    *,
    task_id: int | None,
    mode: str | None,
) -> int:
    conn = connect()
    try:
        new_id: int = conn.execute(
            text(
                """
                INSERT INTO orch_messages (session_id, task_id, role, content, mode, created_at)
                VALUES (:sid, :task_id, :role, :content, :mode, :stamp)
                RETURNING id
                """
            ),
            {
                "sid": session_id,
                "task_id": None if task_id is None else int(task_id),
                "role": role,
                "content": content,
                "mode": mode,
                "stamp": utc_now(),
            },
        ).scalar_one()
        conn.commit()
        return int(new_id)
    finally:
        conn.close()


def _set_message_mode(message_id: int, mode: str) -> None:
    conn = connect()
    try:
        conn.execute(
            text("UPDATE orch_messages SET mode = :mode WHERE id = :id AND mode IS NULL"),
            {"mode": mode, "id": int(message_id)},
        )
        conn.commit()
    finally:
        conn.close()
