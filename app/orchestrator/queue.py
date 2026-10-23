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


def is_status_question(text: str) -> bool:
    """Вопрос о ходе дел без просьбы что-то сделать: отвечается сразу, задачей не ставится."""
    norm = " ".join((text or "").lower().replace("ё", "е").split()) + " "
    if has_imperative(norm) or _RUN_VERBS_RE.search(norm):
        # «прогони дальше, что с лимитом?» - просьба с вопросом, это задача.
        return False
    return any(phrase in norm for phrase in _STATUS_PHRASES)


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
    if any(token in _STOP_TOKENS for token in tokens) and any(obj in norm for obj in _STOP_OBJECTS):
        # «Останови все проекты» — остановить генерацию в Студии, а не только задачу чата.
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
    if any(phrase in norm for phrase in _STEER_PHRASES):
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
    mentions_task = any(marker in norm for marker in _TASK_MARKERS)
    is_question = norm.endswith("?") or (bool(tokens) and tokens[0] in _QUESTION_STARTS)
    asks = norm.endswith("?") and bool(tokens) and tokens[0] in _ASK_STARTS
    if asks or (is_question and not mentions_task):
        return ClassifyResult(
            tier="fast",
            mode=MessageMode.append,
            deterministic=True,
            confidence=1.0,
        )
    return None


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
                if decided is None and task_id is not None and is_status_question(text):
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

    async def _apply(
        self,
        session_id: str,
        text: str,
        decided: ClassifyResult,
        project_id: int | None = None,
    ) -> None:
        if decided.kill_switch:
            job = self.supervisor.request_kill(session_id)
            if job is not None:
                await job
            return
        if decided.mode is MessageMode.interrupt:
            self.supervisor.request_cancel(session_id)
            return
        if decided.mode is MessageMode.steer:
            self.supervisor.request_steer(session_id, text)
            return
        if decided.mode is MessageMode.task:
            await self.supervisor.submit_task(session_id, text, project_id=project_id)
            return
        self.supervisor.note_context(session_id, text)
        await self._stream_fast(session_id, text, decided.tier or "fast", project_id)

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
            return await self.context.messages(text, project_id, app)
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
            await self.explain_failure(session_id, exc, task_id)
            return
        parts = deltas or _token_pieces(response.text)
        message_id = await asyncio.to_thread(
            _insert_message,
            session_id,
            "assistant",
            response.text,
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
            {"role": "assistant", "text": response.text},
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
