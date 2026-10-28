"""Супервизор: единственная точка записи статусов FSM.

R3: planner → dry-run валидаторов → critic (≤2 revise, затем
waiting_approval reason=critic_escalation) → executor только через
actions.execute. Read-only одной parallel_group идут параллельно,
мутации — последовательно. Исключение — подряд идущие платные запуски
``PARALLEL_RUN_ACTIONS`` одной parallel_group в разных проектах: все
сначала одобряются одной карточкой с суммой и списком проектов (чекпоинт на
начале группы), затем идут одновременно, каждый со своими валидаторами и
записью трат. Подряд идущие шаги одного действия в разных проектах (кроме
долгих платных без группы) тоже спрашиваются одной карточкой; её grant
одобряет ровно эти шаги, пока их отпечаток совпадает с планом. Чекпоинт после каждой мутации. Approval —
по max(риск реестра, подсказка плана): план не понижает риск.
Paid-retry guard не даёт
повторно оплатить тот же шаг. Interrupt отменяет неплатный ход,
платный ждёт границы. Kill switch запрещает новые действия и взводит
стоп-ключ, не разрывая платный шаг mid-flight.

Очередь ``MessageMode.task``: вторая задача не стартует, пока в сессии
есть нетерминальная, включая ``paused`` и ``waiting_approval``.
``resume`` и ``grant_approval`` продолжают ту же задачу. Дренаж —
только из терминального статуса. После рестарта ``recover_after_restart``
снова поднимает строки ``queued`` и продолжает оборванное выполнение
с последнего чекпоинта. Платный шаг со строкой ``running`` (исход
неизвестен) сам не исполняется снова: ``waiting_approval`` /
``paid_outcome_unknown``. «Шаг выполнен, продолжить» пишет шаг ``done``
и в ``_paid_done``. «Перезапустить шаг» сначала отдаёт прогноз, потом
одноразовый grant. «Остановить задачу» ставит шаг и задачу в ``failed``.

Dry-run валидаторов (``dry_run=True``) идёт до критика и только читает.
Live — до и после мутации и может остановить задачу. ``project_id``
берётся из строки задачи. Нуля нет. Без id read-only план идёт как раньше,
а платный или мутирующий (``paid``, ``mutating`` или risk = max(реестр, план)
из ``APPROVAL_RISKS``) уходит в ``waiting_approval`` с ``missing_project_id``.

Исход блокировок — ``block_status`` / ``BLOCK_OUTCOMES``. ``deletion_blocked``
терминальный: grant его не исполняет. После grant, до ``_execute_plan``,
план снова проходит ``_screen_plan``. Пустой план чекпоинта на grant/resume —
намеренно ``failed`` с причиной ``empty_plan``, без replan: исполнять нечего.
``missing_project_id`` после grant снова ждёт: id задним числом не пишется.

Грант риска — ключ (задача, индекс, действие, публичные params) и сбрасывается
при ``save_plan`` / steer. Бюджет проверяется на каждой итерации плана и перед
платным шагом. ``ok=False`` пишет шаг ``failed`` и не кладёт его в paid-retry.
Read-only и удаление смотрит реестр; флаги плана только ужесточают.
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import hashlib
import inspect
import json
import re
import time
from datetime import UTC, datetime
from typing import Any

from loguru import logger

from app.orchestrator import app_ref
from app.orchestrator.agents import approval_steps
from app.orchestrator.agents import summarizer as task_summary
from app.orchestrator.agents.executor import effective_risk, run_many, step_is_paid, tighten_paid_step
from app.orchestrator.agents.launch_scope import parallel_needed, refit_parallel, route_multi_launch
from app.orchestrator.agents.planner import PlanSchemaError
from app.orchestrator.agents.light_scope import light_as_coverage
from app.orchestrator.agents.failure_scope import failure_reads
from app.orchestrator.agents.upload_scope import upload_source_from_goal
from app.orchestrator.agents.settings_scope import scope_settings
from app.orchestrator.agents.stubs import (
    ActionBackedExecutor,
    StubCritic,
    StubPlanner,
    UnlinkedActions,
    UnlinkedValidators,
)
from app.orchestrator.agents.task_store import TaskStore
from app.orchestrator.agents.validator_call import run_validator
from app.orchestrator.checkpoints import CheckpointStore
from app.orchestrator.contracts import (
    APPROVAL_RISKS,
    ORCH_REASONING_EFFORT,
    TERMINAL_STATUSES,
    VALIDATOR_CONTEXT_DRY_RUN,
    AgentRole,
    ApprovalDecision,
    ApprovalEvent,
    ApprovalReason,
    BudgetScope,
    CheckpointData,
    PlanResolveRefused,
    PlanStep,
    Risk,
    SpendSnapshot,
    TaskStatus,
    ValidatorResult,
    approval_decision_choices,
    approval_view,
    block_status,
    budget_exhausted_response,
    can_transition,
    ceiling_blocks,
    coerce_risk,
    envelope_body,
    envelope_change_id,
    envelope_skipped,
    envelope_status,
    envelope_warnings,
    escalation_for,
    forecast_cost,
    forecast_unknown,
    frame_soft_delete_state,
    grant_forbidden,
    lookup_action_metadata,
    max_risk,
    metadata_call_gate,
    not_in_whitelist,
    orch_run_id,
    paid_step_identity,
    plan_missing_verify,
    plan_without_client_money,
    r21_result_problem,
    server_forecast_limit,
    step_is_deletion,
    step_is_readonly,
    step_with_registry_paid,
    usable_project_id,
    validator_budget_exhausted,
    validator_missing_run_id,
)
from app.orchestrator.db.locks import write_on_loop
from app.orchestrator.events import EventBus

_DRY_RUN = ("plan_sanity", "no_deletions", "spend_guard")
_RESUME_STATUSES = frozenset({TaskStatus.planning, TaskStatus.critic_review, TaskStatus.executing})
# Кнопка resume у WS4. executing сюда не входит даже с мёртвым прогоном.
_RESUME_ALLOWED_STATUSES = frozenset(
    {TaskStatus.paused, TaskStatus.waiting_approval, TaskStatus.critic_review}
)
_HOLD_STATUSES = frozenset({TaskStatus.paused, TaskStatus.waiting_approval})
_ORPHAN_STATUSES = frozenset(
    {TaskStatus.queued, TaskStatus.planning, TaskStatus.critic_review, TaskStatus.executing}
)
RESTART_ERROR = "interrupted_by_restart"
_RESTART_DETAIL = "Сервер перезапускался, и задача остановилась на полпути."
_RESTART_UNKNOWN = (
    "Шаг {titles} мог успеть выполниться: проверьте результат в Студии, прежде чем повторять, "
    "чтобы не заплатить дважды."
)
_APPR_MARK = "\n\x1eorappr:"
_APPR_ID = re.compile(r"^orch-appr-(\d+)-(\d+)$")
_PLAN_CHANGED = (
    "План изменился после вашего подтверждения: проверьте шаги ещё раз. "
    "Если подтвердите, он выполнится без новой проверки."
)
_PLAN_CHANGED_TWICE = (
    "План снова изменился после подтверждения, поэтому я его не выполнял, чтобы не сделать "
    "то, чего вы не видели."
)
_SCHEMA_REDO_LIMIT = 2
_SCHEMA_FEEDBACK = (
    "Проверка схемы: ответ не разобран ({why}). Верни только JSON вида "
    '{{"steps": [{{"action": "...", "params": {{...}}}}]}}: parallel_group строкой или null, '
    "paid и mutating true/false, params объектом."
)
_SCHEMA_FAILED = (
    "Я трижды просил переделать ответ, но он снова не прошёл проверку, поэтому ничего не выполнялось."
)
_WAITING_TWIN = "Такая задача уже ждёт вашего подтверждения — нажмите «Подтвердить» на карточке выше."
_WAITING_OTHER = task_summary.WAITING_OTHER
_PLAN_LEVEL_CARDS = frozenset(
    {"critic_escalation", "critic_reject", "plan_sanity", "unknown_cost", "steer_not_approved"}
)
_PLAN_BOUND_CARDS = frozenset({*approval_steps.PLAN_CARD_REASONS, "destructive"})
_CRITIC_NO_REASON = "Критик не объяснил причину, поэтому план не отклонён: проверьте шаги и решите сами."
_CRITIC_REJECTED = "Проверяющая модель против этого плана: {why}"
_CRITIC_REVISED = "Проверяющая модель трижды просила переделать план, последнее замечание: {why}"
_CRITIC_REVISED_SLOW = (
    "Проверяющая модель просила переделать план, последнее замечание: {why}. "
    "Чтобы не ждать новых кругов, решение за вами"
)
# Круг «планировщик → критик» на живых моделях идёт ~40 с. После revise, когда с начала
# планирования прошло больше этого, новый круг не начинаем: карточка с замечанием критика.
# Задача 334: «продолжи проект 10» три круга подряд висела в планировании >150 с.
REVISE_TIME_BUDGET_S = 40.0
_OTHER_GATED_STEPS = (
    "Это не весь план: в нём есть и другие шаги с оплатой или подтверждением (ещё {n}), "
    "у каждого будет своя карточка."
)
# Планировщик иногда отдаёт пустой план «нужно отдельное подтверждение человека»: подтверждение
# спрашивает карточка после плана, так что такой отказ - не причина ничего не делать.
_ASKS_APPROVAL_RE = re.compile(
    r"(?:требу|нужн|необходим|сначала|отдельн)\w*\W+(?:\w+\W+){0,3}(?:подтвержд|одобрени|согласи|разрешени)",
    re.IGNORECASE,
)
_APPROVAL_NOTE = (
    "Подтверждение человека для платных и изменяющих шагов спросит карточка после плана. "
    "Не отказывай из-за подтверждения: составь план из действий каталога."
)
# «Предметы всё-таки включи»: включение отключённой ноды, а не платный запуск шага.
_ENABLE_RE = re.compile(r"(?<![а-я])включ(?:и|ить|ите|ай)(?![а-я])")
_ENABLE_NOTE = (
    "Слово «включи» без «запусти/прогони/сгенерируй» — включить отключённую ноду: "
    "node.set_disabled с disabled=false. Шаг конвейера не запускай (step.run_step не добавляй)."
)


def _enable_not_launch(goal: str, steps: list[Any]) -> bool:
    from app.orchestrator.agents.action_catalog import LAUNCH_IDS, wants_launch

    low = (goal or "").lower().replace("ё", "е")
    if not _ENABLE_RE.search(low) or wants_launch(low) or "сгенер" in low or "генерир" in low:
        return False
    return any(_step_action(step) in LAUNCH_IDS for step in steps)


def _step_action(step: Any) -> str:
    if isinstance(step, dict):
        return str(step.get("action") or "")
    return str(getattr(step, "action", "") or "")


_ASKED_APPROVAL = (
    "Планировщик решил, что сначала нужно ваше подтверждение, но его я спрашиваю сам, "
    "карточкой перед запуском. Напишите просьбу ещё раз и назовите "
    "шаг или ноду прямо, например «в проекте 10 запусти шаг предметы»."
)
_HUMAN_PLAN_BLOCKED = (
    "Вы подтвердили план, но он снова не прошёл обязательную проверку ({why}). "
    "Подтверждение эту проверку не снимает, поэтому я план не выполнял."
)
_PROMPT_PROPOSE = (
    "Проверка плана: правку текста промта ноды делай действием prompt.edit_text (append — дописать "
    "в конец, edits — замены фрагментов, text — весь новый текст), не change.propose."
)
#: Начало каждой подсказки ``_screen_plan`` планировщику (каталог, change.propose, схема шагов).
_SCREEN_NOTE_PREFIX = "Проверка плана"


def _critic_context(context: str) -> str:
    """Контекст критику без подсказок проверки плана: они о прошлом, уже отклонённом плане."""
    lines = (context or "").split("\n")
    return "\n".join(line for line in lines if not line.lstrip().startswith(_SCREEN_NOTE_PREFIX)).strip()


_CHECK_RU = {
    "plan_sanity": "перед запуском шага нет чтения состояния",
    "unknown_cost": "стоимость шага неизвестна",
}


def _router_visible_cost(owner: Any) -> float:
    """Цена, которую реальный router уже положил на себя или на ответ.

    ``partial_cost_usd`` у WS5 нет. Берём ``cost_usd`` (поле ``LLMResponse``)
    с обёртки, с ``inner`` и с ``last_response``.
    """
    pending: list[Any] = [owner]
    seen: set[int] = set()
    best = 0.0
    while pending:
        item = pending.pop(0)
        if item is None or id(item) in seen:
            continue
        seen.add(id(item))
        raw = getattr(item, "cost_usd", None)
        if not callable(raw):
            try:
                value = float(raw or 0)
            except (TypeError, ValueError):
                value = 0.0
            if value > best:
                best = value
        # ``last_response`` общего планировщика или критика — ответ прошлого вызова, часто
        # соседней задачи: отменённый вызов свой ответ туда ещё не положил. Его не читаем.
        inner = getattr(item, "inner", None)
        if inner is not None and inner is not item:
            pending.append(inner)
    return best


async def _dequeue_chat_from_queue(project_ids: list[int], session_id: str) -> dict[str, list[int]]:
    """Снять с очереди генерации проекты, которые туда ставил чат этой сессии (аварийный стоп)."""
    from app.db import session_scope
    from app.services.gen_queue import dequeue_chat_projects
    from app.services.orch_tenancy import session_tenant
    from app.services.tenant import tenant_scope

    # Под арендатором сессии (RLS): чужой проект не загрузится и не будет снят,
    # даже если его номер попал в параметры шага.
    with tenant_scope(session_tenant(session_id)):
        async with session_scope() as session:
            return await dequeue_chat_projects(session, project_ids, session_id=session_id)


async def _recheck_dequeued_in_db(project_ids: list[int], session_id: str, since: float) -> list[int]:
    """Проекты, снятые стопом, которые тик очереди всё же успел стартовать (человек их не брал)."""
    from app.db import session_scope
    from app.services.gen_queue import recheck_dequeued
    from app.services.orch_tenancy import session_tenant
    from app.services.tenant import tenant_scope

    with tenant_scope(session_tenant(session_id)):
        async with session_scope() as session:
            return await recheck_dequeued(session, project_ids, session_id=session_id, since=since)


# Повторная проверка после стопа: тик очереди, начатый до стопа, мог стартовать проект позже.
QUEUE_RECHECK_DELAY_SEC = 3.0


def _holders_path(project_id: int) -> Any:
    from app.services.step_cancel import holder_path

    return holder_path(project_id)


def _read_stop_holders(project_id: int) -> tuple[str | None, set[int]]:
    """Метка ключа, который поставил kill, и задачи-держатели (запись переживает рестарт)."""
    path = _holders_path(project_id)
    try:
        data = json.loads(path.read_text(encoding="utf-8") or "{}")
    except FileNotFoundError:
        return None, set()
    except (OSError, ValueError, TypeError):
        logger.exception("orch stop holders of project {} not read", project_id)
        return None, set()
    if not isinstance(data, dict) or not data.get("token"):
        return None, set()
    try:
        tasks = {int(item) for item in data.get("tasks") or []}
    except (TypeError, ValueError):
        tasks = set()
    return str(data["token"]), tasks


def _write_stop_holders(project_id: int, token: str | None, holders: set[int]) -> None:
    """Атомарно: временный файл и os.replace. Пустые держатели — запись удаляется."""
    import os

    path = _holders_path(project_id)
    try:
        if not token or not holders:
            path.unlink(missing_ok=True)
            return
        tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
        tmp.write_text(
            json.dumps({"token": token, "tasks": sorted(int(item) for item in holders)}), encoding="utf-8"
        )
        os.replace(tmp, path)
    except OSError:
        logger.exception("orch stop holders of project {} not written", project_id)


_TRACE_RAW = 500
_AGENT_LAST_FIELDS = ("last_response", "last_reason", "last_from_code", "last_parse_failed")
_TRACE_REASON = 300
_APPROVED_MARK = "\n\x1eorchfp:"
_UNKNOWN_MARK = "\n\x1eorchun:"
_BLOCK_MARK = "\n\x1eorchbr:"
_PAID_OUTCOME_REASONS = frozenset({"paid_outcome_unknown", "paid_unknown_outcome"})
_OUTCOME_HOLD_REASONS = frozenset({*_PAID_OUTCOME_REASONS, "partial_write"})
_PERSISTED_BLOCKS = frozenset(
    {
        ApprovalReason.budget_exhausted.value,
        *_PAID_OUTCOME_REASONS,
    }
)


def _parse_utc(raw: Any) -> datetime | None:
    if not raw:
        return None
    try:
        moment = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


class InvalidTransition(RuntimeError):
    pass


class ApprovalGrantError(RuntimeError):
    """Отказ grant_approval с кодом для WS4: 409 уже решён, 404 чужой id."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class ProjectBindError(RuntimeError):
    """Отказ поздней привязки project_id. 409 — не то ожидание."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _coerce_decision(decision: ApprovalDecision | str | None) -> ApprovalDecision | None:
    if decision is None or decision == "":
        return None
    if isinstance(decision, ApprovalDecision):
        return decision
    return ApprovalDecision(str(decision))


_LLM_TASK: contextvars.ContextVar[int | None] = contextvars.ContextVar("orch_llm_task", default=None)
_LLM_SLOT: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "orch_llm_slot", default=None
)


class Supervisor:
    def __init__(
        self,
        bus: EventBus,
        checkpoints: CheckpointStore,
        tasks: TaskStore | None = None,
        actions: Any | None = None,
        validators: Any | None = None,
        planner: Any | None = None,
        critic: Any | None = None,
        executor: Any | None = None,
    ) -> None:
        self.bus = bus
        self.checkpoints = checkpoints
        self.tasks = tasks or TaskStore()
        self.actions = actions or UnlinkedActions()
        self.validators = validators or UnlinkedValidators()
        self.planner = planner or StubPlanner()
        self.critic = critic or StubCritic()
        self.executor = executor or ActionBackedExecutor(self.actions)
        self.history: list[tuple[int, str]] = []
        self._runs: dict[int, asyncio.Task[Any]] = {}
        self._unpaid: dict[str, asyncio.Task[Any]] = {}
        self._cancel: set[str] = set()
        self._pause: set[str] = set()
        self._kill: set[str] = set()
        self._steer: dict[str, str] = {}
        self._approval: dict[str, str] = {}
        self._session_task: dict[str, int] = {}
        self._change_ids: dict[int, list[str]] = {}
        self._context: dict[int, str] = {}
        self._risk_granted: set[tuple[int, int, str]] = set()
        self._paid_done: dict[int, set[str]] = {}
        self._retry_armed: dict[int, str] = {}
        self._retry_spent: set[tuple[int, str]] = set()
        self._queued: dict[str, list[tuple[int, str]]] = {}
        self._budget_stop: set[int] = set()
        self._shield_orphaned: set[int] = set()
        self._mutating_open: dict[int, int] = {}
        self._paid_open: set[int] = set()
        self._paid_open_count: dict[int, int] = {}
        self._paid_inner: dict[int, set[asyncio.Task[Any]]] = {}
        self._orphan_failed: set[int] = set()
        self._grant_target: dict[int, tuple[int, str]] = {}
        self._step_grant: dict[tuple[int, int], tuple[str | None, str | None]] = {}
        self._group_cards: dict[str, tuple[tuple[int, ...], str]] = {}
        self._paid_outcome_hold: set[int] = set()
        self._paid_unknown_marking: set[int] = set()
        self._pending_paid_unknown: list[tuple[str, int]] = []
        self._step_recorded: set[int] = set()
        self._shield_jobs: set[asyncio.Task[Any]] = set()
        self._block_reason: dict[int, str] = {}
        self._pending_kill_publish: list[tuple[str, int | None]] = []
        self._pending_queue_cancel: list[tuple[str, int]] = []
        self._approved: dict[int, str] = {}
        # Планы, собранные кодом без модели (откат «верни как было»): отпечаток плана задачи.
        self._from_code: dict[int, bool] = {}
        # Снимок last_* планировщика и критика на задачу: объекты общие для всех чатов,
        # а между ответом и чтением супервизора сосед успевает их перезаписать.
        self._planner_meta: dict[int, dict[str, Any]] = {}
        self._critic_meta: dict[int, dict[str, Any]] = {}
        self._code_plans: dict[int, str] = {}
        # Пустой план с готовым ответом («возвращать нечего», «этого чат пока не умеет»): не «не понял».
        self._code_answers: set[int] = set()
        # Правка настроек: «было → станет» по шагу и сколько проектов пропущено (там уже так).
        self._settings_text: dict[int, dict[str, str]] = {}
        self._settings_skipped: dict[int, str] = {}
        # Запуск нескольких проектов участником: окно параллели не меняем, строка на карточке.
        self._launch_note: dict[int, str] = {}
        # Массовый запуск: кого не поставили (жёсткое условие запуска) — первой строкой карточки.
        self._launch_skipped: dict[int, str] = {}
        self._unknown_step: dict[int, dict[str, Any]] = {}
        self._appr_seq: dict[int, int] = {}
        self._appr_open: dict[int, str | None] = {}
        self._appr_owner: dict[str, tuple[int, str]] = {}
        self._appr_decided: dict[str, str | None] = {}
        self._appr_loaded: set[int] = set()
        self._decided_change_id: dict[tuple[int, str], str] = {}
        self._card_action: dict[str, str] = {}
        self._cc_change_id: dict[tuple[int, str], str] = {}
        self._partial_llm_cost: dict[int, float] = {}
        self._grant_locks: dict[int, asyncio.Lock] = {}
        self._inflight_write: dict[int, asyncio.Task[Any]] = {}
        self._mutating_result: dict[int, str] = {}
        self._partial_announced: set[tuple[int, int]] = set()
        self._stop_holders: dict[int, set[int]] = {}
        # Проекты, которым kill задачи поставил stop-ключ (задача на несколько проектов).
        self._kill_stopped: dict[int, set[int]] = {}
        self._stop_external: set[int] = set()
        self._stop_tokens: dict[int, str] = {}
        self.summarizer: Any = task_summary.TaskSummarizer()
        self._notes: dict[int, list[task_summary.StepNote]] = {}
        self._fail_detail: dict[int, str] = {}
        self._plan_feedback: dict[int, tuple[str, str]] = {}
        self._escalated: dict[int, str] = {}
        self._escalation_redo: set[int] = set()
        self._escalation_reshown: set[int] = set()
        self._card_reason: dict[int, str] = {}
        self._human_plan: dict[int, str] = {}
        self._schema_redo: dict[int, int] = {}
        self._stale_cards: set[int] = set()
        self.launch_guard: Any = None
        self.card_meta: Any = None
        self._announced: set[int] = set()
        self._unsettled: set[int] = set()
        self.status_write_budget_s = 30.0
        self.failed_mark_retries = 20
        self.failed_mark_delay_s = 15.0
        self._bind_partial_llm_cost()

    async def start_task(
        self,
        session_id: str,
        goal: str,
        project_id: int | None = None,
    ) -> int:
        self._cancel.discard(session_id)
        self._pause.discard(session_id)
        self._kill.discard(session_id)
        self._steer.pop(session_id, None)
        self._approval.pop(session_id, None)
        task_id = await self._set_status(
            None,
            TaskStatus.queued,
            session_id=session_id,
            goal=goal,
            project_id=usable_project_id(project_id),
        )
        self._session_task[session_id] = task_id
        self._change_ids[task_id] = []
        self._context[task_id] = ""
        self._paid_done[task_id] = set()
        self._runs[task_id] = self._watch_run(
            task_id,
            session_id,
            asyncio.create_task(
                self._drive(task_id, session_id, goal),
                name=f"orch-task-{task_id}",
            ),
        )
        return task_id

    async def wait(self, task_id: int, timeout: float | None = None) -> None:
        await asyncio.wait_for(self._runs[task_id], timeout)

    async def resume(self, task_id: int) -> None:
        row = self.tasks.get(task_id)
        if row is None:
            raise LookupError(task_id)
        live = self._runs.get(task_id)
        if live is not None and not live.done():
            raise InvalidTransition("resume живой задачи")
        data = await asyncio.to_thread(self.checkpoints.load_latest, task_id)
        if data is None:
            raise LookupError(f"нет чекпоинта для задачи {task_id}")
        session_id = str(row["session_id"])
        self._arm_resume(session_id, task_id, data)
        await self._flush_kill_publishes()
        if not data.plan:
            if self._status(task_id) is not TaskStatus.executing:
                await self._set_status(task_id, TaskStatus.executing, session_id=session_id)
            await self._apply_block(task_id, session_id, "empty_plan")
            return
        if self._block_reason.get(task_id) == ApprovalReason.budget_exhausted.value and int(
            data.position
        ) >= len(data.plan):
            self._budget_stop.discard(task_id)
            self._block_reason.pop(task_id, None)
        if self._status(task_id) is not TaskStatus.executing:
            await self._set_status(task_id, TaskStatus.executing, session_id=session_id)
        self._runs[task_id] = self._watch_run(
            task_id,
            session_id,
            asyncio.create_task(
                self._execute_plan(
                    task_id,
                    session_id,
                    list(data.plan),
                    int(data.position),
                    str(row.get("goal") or ""),
                ),
                name=f"orch-resume-{task_id}",
            ),
        )

    def _resolve_project_task(self, session_id: str, task_id: int | None) -> int | None:
        if task_id is None:
            return self.active_task(session_id)
        row = self.tasks.get(int(task_id))
        if row is None or str(row.get("session_id") or "") != session_id:
            return None
        return int(task_id)

    async def bind_project(
        self,
        session_id: str,
        project_id: int,
        *,
        task_id: int | None = None,
    ) -> dict[str, Any]:
        """Записать project_id и снять только ``missing_project_id``.

        Id не пишется внутри ``grant_approval``. Продолжение не выдаёт грант
        риска: платный или рискованный шаг снова ждёт отдельного approve.
        """
        resolved = self._resolve_project_task(session_id, task_id)
        if resolved is None:
            raise ProjectBindError(409, "no such task")
        if resolved != self.active_task(session_id):
            raise ProjectBindError(409, "not the active task")
        if self._status(resolved) is not TaskStatus.waiting_approval:
            raise ProjectBindError(409, "not waiting_approval")
        if self._block_reason.get(resolved) != ApprovalReason.missing_project_id.value:
            raise ProjectBindError(409, "not missing_project_id")
        if resolved in self._budget_stop:
            raise ProjectBindError(409, "budget_exhausted")
        await write_on_loop(self.tasks.set_project_id, resolved, int(project_id))
        self._block_reason.pop(resolved, None)
        await self._resume_after_project_bind(session_id, resolved)
        status = self._status(resolved)
        return {
            "ok": True,
            "session": session_id,
            "task_id": resolved,
            "project_id": int(project_id),
            "status": status.value if status is not None else "",
        }

    def _shown_risk_covered(self, step: PlanStep) -> bool:
        if step.paid or step_is_paid(self.actions, step):
            return False
        if metadata_call_gate(step.action, self.actions) is not None:
            return False
        if frame_soft_delete_state(step.action, self.actions) is not None:
            return False
        if escalation_for(step.action, step.params) is not None:
            return False
        if self._human_plan_required(step.action):
            return False
        return effective_risk(self.actions, step) is Risk.medium

    def _human_plan_required(self, action: str) -> bool:
        probe = getattr(self.actions, "human_plan_required", None)
        if not callable(probe):
            return False
        try:
            return probe(action) is True
        except Exception:
            return True

    def _grant_shown_steps(self, task_id: int, plan: list[PlanStep], start: int) -> None:
        for index in range(max(0, start), len(plan)):
            step = plan[index]
            if self._shown_risk_covered(step):
                self._risk_granted.add(self._risk_grant_key(task_id, index, step))

    def _step_needs_separate_approval(self, step: PlanStep) -> bool:
        """Привязка проекта не заменяет approve риска, paid и destructive."""
        if step.paid or metadata_call_gate(step.action, self.actions):
            return True
        return effective_risk(self.actions, step) in APPROVAL_RISKS

    async def _resume_after_project_bind(self, session_id: str, task_id: int) -> None:
        """Продолжить после снятия ``missing_project_id``, без ``_risk_granted``.

        Чекпоинт (или сохранённый план) снова проходит экран. Платный или
        рискованный шаг остаётся в ``waiting_approval`` с новой причиной и
        новым серверным ``change_id``.
        """
        data = await asyncio.to_thread(self.checkpoints.load_latest, task_id)
        if data is None:
            data = self._checkpoint_from_row(self.tasks.get(task_id) or {})
        if task_id in self._budget_stop:
            self._block_reason[task_id] = ApprovalReason.budget_exhausted.value
            await self.publish_approval_required(
                session_id,
                {"reason": ApprovalReason.budget_exhausted.value, "task_id": task_id},
                task_id=task_id,
                reuse=True,
            )
            return
        if data is None or not data.plan:
            await self._replan_from_grant(task_id, session_id)
            return
        self._arm_resume(session_id, task_id, data)
        await self._set_status(task_id, TaskStatus.executing, session_id=session_id)
        await self._flush_kill_publishes()
        plan = list(data.plan)
        if await self._screen_plan(task_id, session_id, plan):
            return
        position = int(data.position)
        if position < len(plan) and self._step_needs_separate_approval(plan[position]):
            step = plan[position]
            risk = effective_risk(self.actions, step)
            reason = "risk" if risk in APPROVAL_RISKS else "paid"
            self._block_reason[task_id] = reason
            await self._checkpoint(task_id, session_id, plan, position)
            goal = str((self.tasks.get(task_id) or {}).get("goal") or "")
            card = await self._card_extra(task_id, step, goal)
            await self._escalate(
                task_id,
                session_id,
                reason,
                extra={**card, "action": step.action, "risk": risk.value, "plan_risk": step.risk},
            )
            return
        goal = str((self.tasks.get(task_id) or {}).get("goal") or "")
        self._runs[task_id] = self._watch_run(
            task_id,
            session_id,
            asyncio.create_task(
                self._execute_plan(task_id, session_id, plan, position, goal),
                name=f"orch-bind-{task_id}",
            ),
        )

    async def grant_approval(
        self,
        session_id: str,
        decision: ApprovalDecision | str | None = None,
        *,
        retry_grant: bool = False,
        change_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Продолжить с чекпоинта после waiting_approval.

        Терминальная причина из ``BLOCK_OUTCOMES`` не исполняется.
        Перед исполнением план снова проходит ``_screen_plan``. Пустой план
        чекпоинта — намеренно ``failed`` / ``empty_plan``, без replan.
        ``missing_project_id`` не терминальный, но экран снова остановит
        задачу: записать ``project_id`` после старта нечем.
        Грант риска покрывает только шаг чекпоинта и только это действие
        с этими публичными params.

        ``decision`` пустой — прежний путь. Для ``paid_outcome_unknown``
        пустой decision по-прежнему не вызывает ``execute``. Явные значения:
        ``approve`` помечает шаг сделанным и идёт дальше, ``retry_step``
        только отдаёт прогноз, ``deny`` проваливает шаг и задачу.
        ``retry_grant`` — отдельный одноразовый перезапуск после прогноза.
        ``change_id`` клиент может прислать, но идентификатор одобрения
        выбирает только сервер (``orch-appr-<task>-<n>``).

        Один замок на задачу: второй approve/deny того же ``change_id``
        ждёт, пока первый запишет решение, и получает 409 без второго execute.
        """
        await self.adopt_after_restart(session_id, change_id)
        task_id = self._session_task.get(session_id)
        if task_id is None:
            raise LookupError(session_id)
        lock = self._grant_locks.setdefault(int(task_id), asyncio.Lock())
        async with lock:
            if int(task_id) in self._stale_cards:
                return await self._close_stale(int(task_id), session_id)
            return await self._grant_approval_locked(
                session_id,
                decision,
                retry_grant=retry_grant,
                change_id=change_id,
                task_id=task_id,
            )

    async def _grant_approval_locked(
        self,
        session_id: str,
        decision: ApprovalDecision | str | None,
        *,
        retry_grant: bool,
        change_id: str | None,
        task_id: int,
    ) -> dict[str, Any] | None:
        await self._load_appr_state(session_id, task_id)
        self._guard_change_id(
            task_id,
            session_id,
            change_id,
            retry_grant=retry_grant,
            decision=decision,
        )
        if retry_grant and self._retry_grant_is_spent(task_id):
            return {
                "executed": False,
                "reason": "retry_grant_spent",
                "forecast_usd": None,
                "retry_armed": False,
            }
        if self._status(task_id) is not TaskStatus.waiting_approval:
            raise InvalidTransition("grant_approval только из waiting_approval")
        live = self._runs.get(task_id)
        if live is not None and not live.done():
            raise InvalidTransition("grant_approval живой задачи")
        if grant_forbidden(self._block_reason.get(task_id)):
            raise InvalidTransition("grant запрещён: терминальный отказ")
        pending = await asyncio.to_thread(self.checkpoints.load_latest, task_id)
        decided = _coerce_decision(decision)
        if pending is not None and any(
            not_in_whitelist(step.action, self.actions) for step in pending.plan[int(pending.position) :]
        ):
            await self._fail_unlisted(task_id, session_id)
            if decided is ApprovalDecision.deny:
                return {
                    "executed": False,
                    "decision": ApprovalDecision.deny.value,
                    "reason": "not_in_whitelist",
                }
            raise ApprovalGrantError(403, "not_in_whitelist")
        reason = self._block_reason.get(task_id)
        if retry_grant:
            return await self._consume_paid_retry_grant(task_id, session_id)
        if reason == "paid_outcome_unknown" and decided is None:
            return {"executed": False}
        if reason == "paid_outcome_unknown" and decided is not None:
            if decided is ApprovalDecision.approve:
                await self._emit_decision(task_id, session_id, decided)
                await self._approve_paid_unknown(task_id, session_id)
                return {
                    "executed": False,
                    "decision": ApprovalDecision.approve.value,
                    "retry_armed": False,
                }
            if decided is ApprovalDecision.deny:
                await self._emit_decision(task_id, session_id, decided)
                await self._deny_paid_unknown(task_id, session_id)
                return {
                    "executed": False,
                    "decision": ApprovalDecision.deny.value,
                    "retry_armed": False,
                }
            if decided is ApprovalDecision.retry_step:
                await self._emit_decision(task_id, session_id, decided)
                return await self._arm_paid_retry_forecast(task_id, session_id)
            raise InvalidTransition(f"неизвестное decision {decided.value}")
        if reason == "partial_write" and decided is ApprovalDecision.approve:
            await self._emit_decision(task_id, session_id, decided)
            await self._accept_partial_write(task_id, session_id)
            return {
                "executed": False,
                "decision": ApprovalDecision.approve.value,
                "retry_armed": False,
            }
        if reason == "partial_write" and decided is ApprovalDecision.deny:
            await self._emit_decision(task_id, session_id, decided)
            await self._deny_partial_write(task_id, session_id)
            return {
                "executed": False,
                "decision": ApprovalDecision.deny.value,
                "retry_armed": False,
            }
        if decided in {ApprovalDecision.retry_step, ApprovalDecision.deny}:
            raise InvalidTransition("retry_step и deny только для paid_outcome_unknown")
        if (
            reason == ApprovalReason.missing_project_id.value
            and task_id not in self._budget_stop
            and await self._grant_without_project(task_id, session_id, decided)
        ):
            return None
        card = self._card_reason.get(task_id)
        escalated = reason in _PLAN_LEVEL_CARDS or card in _PLAN_LEVEL_CARDS
        bound = escalated or card in _PLAN_BOUND_CARDS
        decided_card = self._appr_open.get(_appr_key(task_id))
        await self._emit_decision(task_id, session_id, decided)
        data = await asyncio.to_thread(self.checkpoints.load_latest, task_id)
        self._budget_stop.discard(task_id)
        if data is None:
            self._clear_budget_block(task_id)
            await self._replan_after_grant(task_id, session_id, bound)
            return
        self._arm_resume(session_id, task_id, data)
        self._clear_budget_block(task_id)
        await self._set_status(task_id, TaskStatus.executing, session_id=session_id)
        await self._flush_kill_publishes()
        plan = list(data.plan)
        if not plan:
            await self._apply_block(task_id, session_id, "empty_plan")
            return
        if escalated:
            if not self._take_escalated(task_id, plan):
                await self._redo_escalation(task_id, session_id)
                return
            if self._block_reason.get(task_id) not in _PERSISTED_BLOCKS:
                self._block_reason.pop(task_id, None)
            self._human_plan[task_id] = _plan_fingerprint(plan)
        if await self._screen_plan(task_id, session_id, plan):
            return
        paid_outcome = self._block_reason.get(task_id) in _PAID_OUTCOME_REASONS
        if not paid_outcome and not self._plan_was_approved(task_id, plan):
            await self._replan_after_grant(task_id, session_id, bound)
            return
        position = int(data.position)
        if paid_outcome:
            if not self._accept_confirmed_paid(task_id, plan, position):
                action = plan[position].action if position < len(plan) else ""
                extra = {"action": action} if action else None
                await self._apply_block(task_id, session_id, "paid_outcome_unknown", extra=extra)
                return
            self._paid_outcome_hold.discard(task_id)
            self._block_reason.pop(task_id, None)
            position += 1
            if self._paid_spend_fills_budget(task_id):
                self._budget_stop.add(task_id)
                await self._checkpoint(task_id, session_id, plan, position)
                await self._apply_block(
                    task_id,
                    session_id,
                    ApprovalReason.budget_exhausted.value,
                )
                return
        elif position < len(plan) and (group := self.card_group(task_id, decided_card, plan)) is not None:
            self._grant_target.pop(task_id, None)
            if not escalated and decided_card:
                self._grant_group(task_id, decided_card, plan, group)
        elif position < len(plan):
            target = self._granted_position(task_id, plan, position, decided_card)
            if not (escalated and self._step_needs_separate_approval(plan[target])):
                self._risk_granted.add(self._risk_grant_key(task_id, target, plan[target]))
                self._snapshot_grant(task_id, target, plan[target])
        if escalated and not paid_outcome:
            self._grant_shown_steps(task_id, plan, position)
        goal = str((self.tasks.get(task_id) or {}).get("goal") or "")
        self._runs[task_id] = self._watch_run(
            task_id,
            session_id,
            asyncio.create_task(
                self._execute_plan(task_id, session_id, plan, position, goal),
                name=f"orch-approve-{task_id}",
            ),
        )

    async def _grant_without_project(
        self,
        task_id: int,
        session_id: str,
        decided: ApprovalDecision | None,
    ) -> bool:
        data = await asyncio.to_thread(self.checkpoints.load_latest, task_id)
        if data is None or not data.plan:
            data = self._checkpoint_from_row(self.tasks.get(task_id) or {})
        plan = list(data.plan) if data is not None else []
        needs = not plan or (
            self._task_project_id(task_id) is None and _plan_missing_project(plan, self.actions)
        )
        if needs and decided is not ApprovalDecision.approve:
            return False
        await self._emit_decision(task_id, session_id, decided)
        if needs or data is None:
            self._block_reason[task_id] = "project_not_selected"
            await self._set_status(task_id, TaskStatus.executing, session_id=session_id)
            await self._fail_closed(task_id, session_id, "project_not_selected")
            return True
        self._arm_resume(session_id, task_id, data)
        self._block_reason.pop(task_id, None)
        mark = _plan_fingerprint(plan)
        self._approved[task_id] = mark
        self._human_plan[task_id] = mark
        self._grant_shown_steps(task_id, plan, int(data.position))
        await self._set_status(task_id, TaskStatus.executing, session_id=session_id)
        await self._flush_kill_publishes()
        if await self._screen_plan(task_id, session_id, plan):
            return True
        goal = str((self.tasks.get(task_id) or {}).get("goal") or "")
        self._runs[task_id] = self._watch_run(
            task_id,
            session_id,
            asyncio.create_task(
                self._execute_plan(task_id, session_id, plan, int(data.position), goal),
                name=f"orch-approve-{task_id}",
            ),
        )
        return True

    async def adopt_after_restart(
        self,
        session_id: str,
        change_id: str | None = None,
        *,
        task_id: int | None = None,
    ) -> int | None:
        match = _APPR_ID.fullmatch(change_id or "")
        current = self._session_task.get(session_id)
        held = int(task_id) if task_id else (int(match.group(1)) if match is not None else 0)
        if held <= 0:
            if current is not None:
                return current
            found = await self._held_rows(session_id)
            if not found:
                return None
            held = int(found[0]["id"])
        if current == held:
            return held
        if current is not None and self.active_task(session_id) is not None:
            return current
        row = await asyncio.to_thread(self.tasks.get, held)
        if row is None or str(row.get("session_id") or "") != session_id:
            return current
        if str(row.get("status") or "") != TaskStatus.waiting_approval.value:
            return current
        live = self._runs.get(held)
        if live is not None and not live.done():
            return current
        self._session_task[session_id] = held
        await self._load_appr_state(session_id, held)
        await self._note_lost_plan(held, row)
        return held

    async def _held_rows(self, session_id: str) -> list[dict[str, Any]]:
        rows = await asyncio.to_thread(self.tasks.list_open)
        return [
            row
            for row in rows
            if str(row.get("session_id") or "") == session_id
            and str(row.get("status") or "") == TaskStatus.waiting_approval.value
        ]

    async def _note_lost_plan(self, task_id: int, row: dict[str, Any]) -> None:
        data = await asyncio.to_thread(self.checkpoints.load_latest, task_id)
        if data is None:
            data = self._checkpoint_from_row(row)
        if data is not None:
            if task_id not in self._block_reason:
                self._restore_block(task_id, data.context or "")
            return
        if not self._appr_open.get(_appr_key(task_id)):
            return
        steps = await asyncio.to_thread(self.tasks.list_steps, task_id)
        if any(str(step.get("status") or "") in {"running", "partial"} for step in steps):
            return
        self._stale_cards.add(task_id)

    async def _close_stale(self, task_id: int, session_id: str) -> dict[str, Any]:
        self._stale_cards.discard(task_id)
        change_id = self._appr_open.get(_appr_key(task_id))
        await self._emit_decision(task_id, session_id, ApprovalDecision.deny)
        self._block_reason.pop(task_id, None)
        self._budget_stop.discard(task_id)
        await self._set_status(
            task_id,
            TaskStatus.cancelled,
            session_id=session_id,
            error=task_summary.APPROVAL_STALE,
        )
        logger.warning("orch approval after restart: task {} closed, saved plan is gone", task_id)
        return {
            "executed": False,
            "decision": ApprovalDecision.deny.value,
            "reason": task_summary.APPROVAL_STALE,
            "retry_armed": False,
            "change_id": change_id,
        }

    def _take_escalated(self, task_id: int, plan: list[PlanStep]) -> bool:
        expected = self._escalated.pop(task_id, None) or _plan_fingerprint(self._stored_plan(task_id))
        if _plan_fingerprint(plan) != expected:
            return False
        self._approved[task_id] = expected
        return True

    async def _replan_after_grant(self, task_id: int, session_id: str, bound: bool) -> None:
        if bound:
            await self._redo_escalation(task_id, session_id)
            return
        await self._replan_from_grant(task_id, session_id)

    async def _redo_escalation(self, task_id: int, session_id: str) -> None:
        if task_id in self._escalation_redo:
            self._block_reason[task_id] = "plan_changed"
            self._fail_detail[task_id] = _PLAN_CHANGED_TWICE
            if self._status(task_id) is TaskStatus.waiting_approval:
                await self._set_status(task_id, TaskStatus.executing, session_id=session_id)
            await self._fail_closed(task_id, session_id, "plan_changed")
            return
        self._escalation_redo.add(task_id)
        self._escalation_reshown.discard(task_id)
        self._block_reason.pop(task_id, None)
        await self._replan_from_grant(task_id, session_id)

    def _redo_card_due(self, task_id: int, plan: list[PlanStep]) -> bool:
        if task_id not in self._escalation_redo or task_id in self._escalation_reshown:
            return False
        if _free_read_plan(plan, self.actions):
            return False
        self._escalation_reshown.add(task_id)
        return True

    async def _escalate_plan(
        self,
        task_id: int,
        session_id: str,
        plan: list[PlanStep],
        warning: str | None = None,
    ) -> None:
        self._escalated[task_id] = _plan_fingerprint(plan)
        await self._checkpoint(task_id, session_id, plan, 0)
        extra = {"warnings": [warning]} if warning else None
        await self._apply_block(task_id, session_id, ApprovalReason.critic_escalation.value, extra=extra)

    async def reject_approval(self, session_id: str, *, change_id: str | None = None) -> dict[str, Any]:
        await self.adopt_after_restart(session_id, change_id)
        task_id = self._session_task.get(session_id)
        if task_id is None:
            raise LookupError(session_id)
        lock = self._grant_locks.setdefault(int(task_id), asyncio.Lock())
        async with lock:
            if int(task_id) in self._stale_cards:
                return await self._close_stale(int(task_id), session_id)
            await self._load_appr_state(session_id, task_id)
            self._guard_change_id(
                task_id,
                session_id,
                change_id,
                retry_grant=False,
                decision=ApprovalDecision.deny,
            )
            if self._status(task_id) is not TaskStatus.waiting_approval:
                raise InvalidTransition("reject только из waiting_approval")
            live = self._runs.get(task_id)
            if live is not None and not live.done():
                raise InvalidTransition("reject живой задачи")
            decided = change_id or self._appr_open.get(int(task_id))
            reason = self._block_reason.get(task_id)
            await self._emit_decision(task_id, session_id, ApprovalDecision.deny)
            if reason in _PAID_OUTCOME_REASONS:
                await self._deny_paid_unknown(task_id, session_id)
            elif reason == "partial_write":
                await self._deny_partial_write(task_id, session_id)
            else:
                self._budget_stop.discard(task_id)
                self._block_reason.pop(task_id, None)
                # Отказ — решение человека, а не сбой: задача отменена, без task_failed и красного значка.
                await self._set_status(task_id, TaskStatus.cancelled, session_id=session_id, error="rejected")
            return {
                "executed": False,
                "decision": ApprovalDecision.deny.value,
                "retry_armed": False,
                "change_id": decided,
            }

    async def _fail_unlisted(self, task_id: int, session_id: str) -> None:
        """Отказ белого списка завершает задачу. Карточка больше не открыта."""
        self._block_reason[task_id] = "not_in_whitelist"
        self._appr_open[_appr_key(task_id)] = None
        if self._status(task_id) not in TERMINAL_STATUSES:
            await self._set_status(
                task_id,
                TaskStatus.failed,
                session_id=session_id,
                error="not_in_whitelist",
            )

    async def _replan_from_grant(self, task_id: int, session_id: str) -> None:
        """Эскалация случилась до approve: цикл planner → critic заново, с позиции 0."""
        self._approved.pop(task_id, None)
        self._cancel.discard(session_id)
        self._pause.discard(session_id)
        self._steer.pop(session_id, None)
        self._approval.pop(session_id, None)
        self._session_task[session_id] = task_id
        goal = str((self.tasks.get(task_id) or {}).get("goal") or "")
        self._runs[task_id] = self._watch_run(
            task_id,
            session_id,
            asyncio.create_task(
                self._drive(task_id, session_id, goal),
                name=f"orch-replan-{task_id}",
            ),
        )

    def _plan_was_approved(self, task_id: int, plan: list[PlanStep]) -> bool:
        mark = self._approved.get(task_id)
        return mark is not None and mark == _plan_fingerprint(plan)

    def _arm_resume(self, session_id: str, task_id: int, data: CheckpointData) -> None:
        self._cancel.discard(session_id)
        self._pause.discard(session_id)
        self._kill.discard(session_id)
        self._steer.pop(session_id, None)
        self._approval.pop(session_id, None)
        self._session_task[session_id] = task_id
        self._change_ids[task_id] = list(data.change_ids)
        visible, packed = _split_appr_context(data.context or "")
        visible, mark = _unpack_approved(visible)
        self._context[task_id] = visible
        if task_id not in self._appr_loaded:
            self._merge_appr_pack(task_id, session_id, packed)
        if mark:
            self._approved[task_id] = mark
        self._restore_block(task_id, data.context or "")
        self._remember_paid(task_id)
        self._clear_project_stop(task_id)

    def _clear_project_stop(self, task_id: int) -> None:
        """Снять stop только если его поставил kill этой задачи и держателей больше нет.

        ``user_stop``, stop другой сессии и файл, который уже был до нашего
        kill, остаются. Задача на несколько проектов снимает ключ каждого из них.
        """
        projects = set(self._kill_stopped.pop(int(task_id), set()))
        own = self._task_project_id(task_id)
        if own is not None:
            projects.add(own)
        # После рестарта памяти нет: проекты берём из шагов задачи, а держателя — из файла рядом
        # с ключом. Чужой ключ (нет нашей записи или он новее неё) не снимается.
        projects.update(self._step_projects(task_id, running_only=False))
        for project_id in sorted(projects):
            self._clear_one_project_stop(task_id, project_id)

    def _own_stop(self, project_id: int) -> tuple[str, set[int]] | None:
        """Метка и держатели, если ТЕКУЩИЙ ключ проекта поставил kill чата, иначе None.

        Ключ сверяется по содержимому (метке kill), не по времени файлов: ключ, который
        воркер снял, а человек поставил заново, — чужой, даже если запись держателей есть.
        """
        from app.services.step_cancel import stop_token

        current = stop_token(project_id)
        token = self._stop_tokens.get(project_id)
        holders = self._stop_holders.get(project_id)
        if token is None:
            token, holders = _read_stop_holders(project_id)
        if current is None or token is None or current != token:
            # Ключа нет или он чужой: наша запись о нём устарела. Чужой ключ не трогаем.
            self._stop_tokens.pop(project_id, None)
            self._stop_holders.pop(project_id, None)
            if token is not None:
                _write_stop_holders(project_id, None, set())
            return None
        holders = set(holders or set())
        self._stop_tokens[project_id] = token
        self._stop_holders[project_id] = holders
        return token, holders

    def _clear_one_project_stop(self, task_id: int, project_id: int) -> None:
        own = self._own_stop(project_id)
        if own is None:
            return
        token, holders = own
        if int(task_id) not in holders:
            return
        holders.discard(int(task_id))
        if holders:
            self._stop_holders[project_id] = holders
            _write_stop_holders(project_id, token, holders)
            return
        self._stop_holders.pop(project_id, None)
        self._stop_tokens.pop(project_id, None)
        from app.services.step_cancel import clear_stop

        clear_stop(project_id)

    def _note_kill_stop(self, project_id: int, task_id: int | None) -> None:
        """Записать, что kill этой задачи поставил stop, либо что stop чужой (тогда не держим)."""
        import uuid

        from app.services.step_cancel import is_stop_requested, request_stop

        own = self._own_stop(project_id)
        if own is None:
            if is_stop_requested(project_id):
                # Ключ человека или другого процесса: kill его не присваивает.
                self._stop_external.add(project_id)
                return
            self._stop_external.discard(project_id)
            token = f"orchestrator_kill:{uuid.uuid4().hex}"
            request_stop(project_id, reason="orchestrator_kill", token=token)
            holders: set[int] = set()
        else:
            token, holders = own
        if task_id is not None:
            holders.add(int(task_id))
        self._stop_tokens[project_id] = token
        self._stop_holders[project_id] = holders
        _write_stop_holders(project_id, token, holders)

    def _restore_block(self, task_id: int, raw: str) -> None:
        reason = _unpack_block(raw)
        if reason not in _PERSISTED_BLOCKS:
            return
        self._block_reason[task_id] = reason
        if reason == ApprovalReason.budget_exhausted.value:
            self._budget_stop.add(task_id)
        if reason in _PAID_OUTCOME_REASONS:
            self._paid_outcome_hold.add(task_id)

    def _clear_budget_block(self, task_id: int) -> None:
        """Грант снимает бюджетный стоп уже после восстановления чекпоинта."""
        self._budget_stop.discard(task_id)
        if self._block_reason.get(task_id) == ApprovalReason.budget_exhausted.value:
            self._block_reason.pop(task_id, None)

    def _drop_block_text(self, task_id: int) -> None:
        """Снять метку блока из памяти контекста. Видимая оценка шага остаётся."""
        prev, _packed = _split_appr_context(self._context.get(task_id, ""))
        visible, _mark = _unpack_approved(prev)
        self._context[task_id] = visible

    def _block_holds_resume(self, task_id: int, data: CheckpointData) -> bool:
        """Пока впереди платный или бюджетный шаг, resume без гранта не идёт."""
        reason = self._block_reason.get(task_id)
        if reason not in _PERSISTED_BLOCKS:
            return False
        return int(data.position) < len(data.plan)

    def stop_closes_card(self, task_id: int) -> bool:
        """True — «стоп» сам закроет карточку: она ждёт решения после неизвестного исхода платного шага."""
        return (
            self._status(task_id) is TaskStatus.waiting_approval
            and self._block_reason.get(task_id) in _PAID_OUTCOME_REASONS
        )

    def request_cancel(self, session_id: str) -> None:
        """Взвести cancel-флаг и отменить текущий неплатный шаг, если он есть.

        Платный шаг уже в полёте не откатывается: задача уходит в
        ``waiting_approval`` с причиной ``paid_outcome_unknown``.
        """
        self._cancel.add(session_id)
        task_id = self._session_task.get(session_id)
        if task_id is not None:
            self.tasks.set_flag(task_id, cancel_requested=1)
        if task_id is not None and self.stop_closes_card(task_id):
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                return
            self._spawn(self._deny_paid_outcome(session_id, task_id))
            return
        if task_id is not None and task_id in self._paid_open:
            self._paid_outcome_hold.add(task_id)
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                self._pending_paid_unknown.append((session_id, task_id))
                return
            self._spawn(self._mark_paid_outcome_unknown(session_id, task_id))
            return
        inflight = self._unpaid.get(session_id)
        if inflight is not None and not inflight.done():
            inflight.cancel()

    def request_kill(self, session_id: str) -> asyncio.Task[Any] | None:
        """Kill switch: неплатное — сразу, новые действия запрещены.

        Платный шаг уже в полёте не откатывается: задача уходит в
        ``waiting_approval`` с причиной ``paid_outcome_unknown``.
        Если у задачи есть ``project_id``, сразу зовётся ``request_stop``:
        файл ``project_{id}.stop``. Событие тогда несёт ``stop_key_set=true``
        и имя этого файла. Без проекта ключ остаётся ``pending``.
        Возвращает задачу публикации, если цикл уже
        есть: очередь её дожидается, иначе запись sqlite могла бы отстать
        от ``drain``. Без цикла событие остаётся в очереди до resume/grant.
        """
        self._kill.add(session_id)
        self.request_cancel(session_id)
        self._cancel_open_mutating(session_id)
        queued = list(self._queued.pop(session_id, []))
        task_id = self._session_task.get(session_id)
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            logger.warning("kill_switch for {} queued: no running loop", session_id)
            self._pending_kill_publish.append((session_id, task_id))
            self._pending_queue_cancel.extend((session_id, queued_id) for queued_id, _goal in queued)
            return None
        for queued_id, _goal in queued:
            self._spawn(self._cancel_queued(session_id, queued_id))
        return self._spawn(self._publish_kill(session_id, task_id))

    def _cancel_open_mutating(self, session_id: str) -> None:
        """Kill гасит внешний прогон mutating-шага, но не сам inner.

        Новые шаги не стартуют. Запись, которая уже идёт, дописывается,
        и её ``change_id`` садится на строку ``partial`` в done-callback.
        Платный шаг сюда не попадает: его держит ``_paid_open``.
        """
        task_id = self._session_task.get(session_id)
        if task_id is None or task_id in self._paid_open or task_id not in self._mutating_open:
            return
        self._shield_orphaned.add(task_id)
        run = self._runs.get(task_id)
        if run is not None and not run.done():
            run.cancel()

    async def _cancel_queued(self, session_id: str, task_id: int) -> None:
        """Задача, стоявшая в очереди на момент kill, не должна стартовать позже."""
        if self._status(task_id) is TaskStatus.queued:
            await self._set_status(task_id, TaskStatus.cancelled, session_id=session_id)

    def request_pause(self, session_id: str) -> None:
        self._pause.add(session_id)
        task_id = self._session_task.get(session_id)
        if task_id is not None:
            self.tasks.set_flag(task_id, pause_requested=1)

    def request_steer(self, session_id: str, text: str) -> None:
        self._steer[session_id] = text
        task_id = self._session_task.get(session_id)
        if task_id is not None:
            self.tasks.set_flag(task_id, steer_text=text)

    def note_context(self, session_id: str, text: str) -> None:
        """Реплика ``append`` остаётся в контексте, план сама не меняет.

        Следующий planner (steer) читает этот текст. Без активной задачи
        писать некуда.
        """
        task_id = self.active_task(session_id)
        note = (text or "").strip()
        if task_id is None or not note:
            return
        self._context[task_id] = (self._context.get(task_id, "") + "\n" + note).strip()

    def request_approval(self, session_id: str, reason: str) -> None:
        """Попросить человека. Статус сменится на границе шага, не mid-flight.

        Для исчерпания суб-бюджета reason='budget_exhausted': FSM остаётся
        waiting_approval, отдельный статус не заводится.
        """
        self._approval[session_id] = reason

    def resume_allowed(self, task_id: int) -> bool:
        """true только для paused|waiting_approval|critic_review с чекпоинтом и без живого прогона."""
        status = self._status(task_id)
        if status not in _RESUME_ALLOWED_STATUSES:
            return False
        live = self._runs.get(task_id)
        if live is not None and not live.done():
            return False
        return self.checkpoints.load_latest(task_id) is not None

    def session_snapshot(self, session_id: str) -> dict[str, Any]:
        """Снимок активной задачи сессии для GET статуса и сверки с agent_status."""
        task_id = self._session_task.get(session_id)
        if task_id is None:
            task_id = self.active_task(session_id)
        if task_id is None:
            return {
                "session": session_id,
                "task_id": None,
                "status": "",
                "resume_allowed": False,
            }
        status = self._status(task_id)
        return {
            "session": session_id,
            "task_id": task_id,
            "status": status.value if status is not None else "",
            "resume_allowed": self.resume_allowed(task_id),
        }

    def active_task(self, session_id: str) -> int | None:
        task_id = self._session_task.get(session_id)
        if task_id is None:
            return None
        status = self._status(task_id)
        if status is None or status in TERMINAL_STATUSES:
            return None
        return task_id

    async def submit_task(
        self,
        session_id: str,
        goal: str,
        project_id: int | None = None,
    ) -> int:
        """Новая задача (``MessageMode.task``). Активную не меняет: встаёт в очередь.

        Активная — любая нетерминальная, в том числе ``paused`` и
        ``waiting_approval``. Рядом с ней вторая не стартует.
        """
        twin = await self._waiting_twin(session_id, goal)
        if twin is not None:
            await self._point_to_card(session_id, twin)
            return twin
        active = self.active_task(session_id)
        bound = usable_project_id(project_id)
        if active is not None:
            task_id = await write_on_loop(
                self.tasks.insert,
                session_id,
                goal,
                TaskStatus.queued.value,
                project_id=bound,
            )
            self._queued.setdefault(session_id, []).append((task_id, goal))
            return task_id
        return await self.start_task(session_id, goal, project_id=bound)

    async def _waiting_twin(self, session_id: str, goal: str) -> int | None:
        words = _goal_words(goal)
        if not words:
            return None
        for row in await self._held_rows(session_id):
            if _same_goal(words, _goal_words(str(row.get("goal") or ""))):
                return int(row["id"])
        return None

    async def _point_to_card(self, session_id: str, task_id: int) -> None:
        await self.adopt_after_restart(session_id, task_id=task_id)
        await self._load_appr_state(session_id, task_id)
        change_id = self._appr_open.get(_appr_key(task_id))
        try:
            await asyncio.to_thread(task_summary.store_message, session_id, task_id, _WAITING_TWIN)
        except Exception:
            logger.exception("orch twin notice store failed for task {}", task_id)
        await self.bus.publish(
            session_id,
            "message",
            {
                "role": "assistant",
                "text": _WAITING_TWIN,
                "kind": "approval_pending",
                "task_id": task_id,
                "change_id": change_id,
                "focus_approval": True,
            },
            task_id=task_id,
        )

    async def _mention_waiting_card(self, task_id: int, session_id: str) -> None:
        others = [row for row in await self._held_rows(session_id) if int(row["id"]) != int(task_id)]
        if not others:
            return
        goal = " ".join(str(others[0].get("goal") or "").split())
        if len(goal) > 120:
            goal = goal[:119] + "…"
        note = _WAITING_OTHER.format(goal=goal or "без названия")
        previous = self._fail_detail.get(task_id, "")
        self._fail_detail[task_id] = (previous + " " + note).strip()

    async def recover_after_restart(self) -> list[int]:
        """Поднять незавершённые задачи после рестарта процесса.

        ``queued`` возвращаются в память очереди. Если сессия свободна,
        стартует самая старая. ``executing`` продолжается с чекпоинта.
        ``planning`` и ``critic_review`` заново проходят dry-run и критика:
        ``execute`` только после ``approve``. ``paused`` и ``waiting_approval``
        сами не стартуют и не отпускают очередь сессии.

        Платный шаг с ``orch_steps.status=running`` доходит до ``_execute_plan``
        и там останавливается: ``waiting_approval`` / ``paid_outcome_unknown``.
        ``execute`` не вызывается, пока человек не выберет кнопку.
        Задача с выставленным stop-ключом проекта не стартует.
        """
        rows = await asyncio.to_thread(self.tasks.list_open)
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            grouped.setdefault(str(row["session_id"]), []).append(row)
        started: list[int] = []
        for session_id, group in grouped.items():
            started.extend(await self._recover_session(session_id, group))
        return started

    async def settle_after_restart(self) -> list[int]:
        rows = await asyncio.to_thread(self.tasks.list_open)
        closed: list[int] = []
        for row in rows:
            task_id = int(row["id"])
            status = TaskStatus(str(row["status"]))
            if status not in _ORPHAN_STATUSES:
                continue
            live = self._runs.get(task_id)
            if live is not None and not live.done():
                continue
            session_id = str(row["session_id"])
            steps = await asyncio.to_thread(self.tasks.list_steps, task_id)
            unknown = [step for step in steps if str(step.get("status") or "") == "running"]
            detail = _RESTART_DETAIL
            if unknown:
                titles = ", ".join(
                    f"«{task_summary.step_title(str(step['action_name']))}»" for step in unknown
                )
                detail += " " + _RESTART_UNKNOWN.format(titles=titles)
            self._fail_detail[task_id] = detail
            try:
                await self._set_status(
                    task_id,
                    TaskStatus.failed,
                    session_id=session_id,
                    error=RESTART_ERROR,
                )
                if status is TaskStatus.queued:
                    await self._announce_result(task_id, session_id, TaskStatus.failed, RESTART_ERROR)
                await self.bus.publish(session_id, "task_failed", {"error": RESTART_ERROR}, task_id=task_id)
            except Exception:
                logger.exception("orch restart: task {} not closed", task_id)
                continue
            closed.append(task_id)
        return closed

    async def expire_stale_approvals(self, *, now: datetime | None = None) -> list[int]:
        from app.settings import settings

        ttl = int(settings.action_layer_approval_ttl_s)
        moment = now or datetime.now(UTC)
        rows = await asyncio.to_thread(self.tasks.list_open)
        closed: list[int] = []
        for row in rows:
            if str(row.get("status")) != TaskStatus.waiting_approval.value:
                continue
            since = _parse_utc(row.get("updated_at"))
            if since is None or (moment - since).total_seconds() < ttl:
                continue
            task_id = int(row["id"])
            try:
                if await self._expire_approval(task_id, str(row["session_id"])):
                    closed.append(task_id)
            except Exception:
                logger.exception("orch approval expiry: task {} not closed", task_id)
        return closed

    async def _expire_approval(self, task_id: int, session_id: str) -> bool:
        lock = self._grant_locks.setdefault(int(task_id), asyncio.Lock())
        async with lock:
            if self._status(task_id) is not TaskStatus.waiting_approval:
                return False
            live = self._runs.get(task_id)
            if live is not None and not live.done():
                return False
            data = await asyncio.to_thread(self.checkpoints.load_latest, task_id)
            if data is not None and task_id not in self._block_reason:
                self._restore_block(task_id, data.context or "")
            if self._block_reason.get(task_id) in _OUTCOME_HOLD_REASONS:
                return False
            steps = await asyncio.to_thread(self.tasks.list_steps, task_id)
            if any(str(step.get("status") or "") in {"running", "partial"} for step in steps):
                return False
            changed = [
                f"«{task_summary.step_title(str(step.get('action_name') or ''))}»"
                for step in steps
                if str(step.get("status") or "") == "done" and step.get("mutating")
            ]
            await self._load_appr_state(session_id, task_id)
            await self._emit_decision(task_id, session_id, ApprovalDecision.deny)
            self._budget_stop.discard(task_id)
            self._block_reason.pop(task_id, None)
            self._retry_armed.pop(task_id, None)
            self._paid_outcome_hold.discard(task_id)
            self._notes.pop(task_id, None)
            if changed:
                self._fail_detail[task_id] = "До этого успели выполнить: " + ", ".join(changed) + "."
            await self._set_status(
                task_id,
                TaskStatus.cancelled,
                session_id=session_id,
                error=task_summary.APPROVAL_EXPIRED,
            )
            logger.warning("orch approval expiry: task {} cancelled, approval not given in time", task_id)
            return True

    async def _recover_session(self, session_id: str, rows: list[dict[str, Any]]) -> list[int]:
        tracked = self._tracked_ids(session_id)
        fresh = [row for row in rows if int(row["id"]) not in tracked]
        if not fresh:
            return []
        queued = [row for row in fresh if str(row["status"]) == TaskStatus.queued.value]
        held = [row for row in fresh if TaskStatus(str(row["status"])) in _HOLD_STATUSES]
        interrupted = [row for row in fresh if TaskStatus(str(row["status"])) in _RESUME_STATUSES]
        queued_pairs = [(int(row["id"]), str(row.get("goal") or "")) for row in queued]
        if interrupted:
            primary = interrupted[0]
            task_id = int(primary["id"])
            self._queued[session_id] = self._merge_queued(session_id, queued_pairs)
            if self._stop_key_blocks_recover(primary):
                self._session_task[session_id] = task_id
                return []
            status = TaskStatus(str(primary["status"]))
            saved = await self._load_saved_plan(task_id, primary)
            if saved is None and status is TaskStatus.planning:
                self._spawn_drive(task_id, session_id, str(primary.get("goal") or ""))
                return [task_id]
            if saved is None and status is TaskStatus.executing:
                self._session_task[session_id] = task_id
                await self._fail_closed(task_id, session_id, "empty_plan")
                return [task_id]
            if await self._spawn_from_checkpoint(task_id, primary):
                return [task_id]
            self._session_task[session_id] = task_id
            self._fail_detail[task_id] = _RESTART_DETAIL
            await self._fail_closed(task_id, session_id, RESTART_ERROR)
            return [task_id]
        if held:
            held_id = int(held[0]["id"])
            self._session_task[session_id] = held_id
            self._queued[session_id] = self._merge_queued(session_id, queued_pairs)
            await self._load_appr_state(session_id, held_id)
            if str(held[0]["status"]) == TaskStatus.waiting_approval.value:
                await self._note_lost_plan(held_id, held[0])
            return []
        if not queued:
            return []
        first, *rest = queued
        rest_pairs = [(int(row["id"]), str(row.get("goal") or "")) for row in rest]
        self._queued[session_id] = self._merge_queued(session_id, rest_pairs)
        task_id = int(first["id"])
        if self._stop_key_blocks_recover(first):
            self._session_task[session_id] = task_id
            return []
        self._spawn_drive(task_id, session_id, str(first.get("goal") or ""))
        return [task_id]

    def _stop_key_blocks_recover(self, row: dict[str, Any]) -> bool:
        """Stop-файл проекта запрещает resume и старт очереди после рестарта."""
        raw = row.get("project_id")
        try:
            project_id = int(raw)
        except (TypeError, ValueError):
            return False
        if project_id <= 0:
            return False
        from app.services.step_cancel import is_stop_requested

        return is_stop_requested(project_id)

    async def _load_saved_plan(self, task_id: int, row: dict[str, Any]) -> CheckpointData | None:
        data = await asyncio.to_thread(self.checkpoints.load_latest, task_id)
        if data is not None and data.plan:
            return data
        fallback = self._checkpoint_from_row(row)
        if fallback is not None and fallback.plan:
            return fallback
        return None

    async def _spawn_from_checkpoint(self, task_id: int, row: dict[str, Any]) -> bool:
        running = self._runs.get(task_id)
        if running is not None and not running.done():
            return True
        data = await asyncio.to_thread(self.checkpoints.load_latest, task_id)
        if data is None:
            data = self._checkpoint_from_row(row)
        if data is None:
            logger.warning(
                "orch recover {}: нет чекпоинта, задача в статусе {} закрывается",
                task_id,
                row.get("status"),
            )
            return False
        session_id = str(row["session_id"])
        self._arm_resume(session_id, task_id, data)
        self._runs[task_id] = self._watch_run(
            task_id,
            session_id,
            asyncio.create_task(
                self._continue_after_restart(task_id, session_id, data, str(row.get("goal") or "")),
                name=f"orch-recover-{task_id}",
            ),
        )
        return True

    async def _continue_after_restart(
        self,
        task_id: int,
        session_id: str,
        data: CheckpointData,
        goal: str,
    ) -> None:
        current = self._status(task_id)
        if current in {TaskStatus.planning, TaskStatus.critic_review}:
            await self._review_saved_plan(task_id, session_id, data, goal)
            return
        if current is not TaskStatus.executing:
            return
        if not data.plan:
            await self._apply_block(task_id, session_id, "empty_plan")
            return
        await self._execute_plan(task_id, session_id, list(data.plan), int(data.position), goal)

    async def _review_saved_plan(
        self,
        task_id: int,
        session_id: str,
        data: CheckpointData,
        goal: str,
    ) -> None:
        """Черновик после рестарта не исполняется, пока dry-run и критик не approve."""
        plan = list(data.plan)
        context, packed = _split_appr_context(data.context or self._context.get(task_id, ""))
        context, mark = _unpack_approved(context)
        if task_id not in self._appr_loaded:
            self._merge_appr_pack(task_id, session_id, packed)
        if mark:
            self._approved[task_id] = mark
        self._context[task_id] = context
        revises = 0
        while True:
            if await self._screen_plan(task_id, session_id, plan):
                return
            if self._status(task_id) is TaskStatus.planning:
                await self._set_status(task_id, TaskStatus.critic_review, session_id=session_id)
            verdict = await self._review_plan(task_id, session_id, plan, context)
            if verdict is None:
                return
            if verdict == "approve":
                self._approved[task_id] = _plan_fingerprint(plan)
                await self._set_status(task_id, TaskStatus.executing, session_id=session_id)
                await self._execute_plan(task_id, session_id, plan, 0, goal)
                return
            if verdict in {"reject", "escalate"}:
                await self._critic_block(task_id, session_id, plan, verdict)
                return
            revises += 1
            if revises > 2:
                await self._critic_revised_out(task_id, session_id, plan)
                return
            context = (context + "\n" + self._critic_feedback(task_id, verdict)).strip()
            self._context[task_id] = context
            plan = await self._make_plan(task_id, session_id, goal, context)
            if plan is None:
                return

    def _critic_reason(self, task_id: int) -> str:
        return " ".join(str(self._critic_last(task_id, "last_reason") or "").split())

    def _critic_note(self, task_id: int, pattern: str) -> str | None:
        why = approval_steps.humanize_reason(self._critic_reason(task_id))
        return pattern.format(why=why) if why else None

    def _critic_feedback(self, task_id: int, verdict: str) -> str:
        why = self._critic_reason(task_id)
        return f"critic: {verdict}" + (f" — {why}" if why else "")

    async def _critic_block(self, task_id: int, session_id: str, plan: list[PlanStep], verdict: str) -> None:
        if verdict == "escalate":
            if await self._blocked_step_card(task_id, session_id, plan, _CRITIC_NO_REASON):
                return
            await self._escalate_plan(task_id, session_id, plan, _CRITIC_NO_REASON)
            return
        note = self._critic_note(task_id, _CRITIC_REJECTED)
        if await self._blocked_step_card(task_id, session_id, plan, note):
            return
        await self._checkpoint(task_id, session_id, plan, 0)
        await self._apply_block(
            task_id, session_id, "critic_reject", extra={"warnings": [note]} if note else None
        )

    async def _critic_revised_out(
        self, task_id: int, session_id: str, plan: list[PlanStep], *, slow: bool = False
    ) -> None:
        note = self._critic_note(task_id, _CRITIC_REVISED_SLOW if slow else _CRITIC_REVISED)
        if await self._blocked_step_card(task_id, session_id, plan, note):
            return
        await self._escalate_plan(task_id, session_id, plan, note)

    async def _blocked_step_card(
        self, task_id: int, session_id: str, plan: list[PlanStep], note: str | None
    ) -> bool:
        """Шаг плана, который условия запуска уже не пустят: его карточка вместо карточки критика.

        Карточка «Проверяющая модель против этого плана» подтверждаема: человек одобряет план
        поверх возражения, и только потом шаг спрашивает свою карточку с условиями запуска.
        Если условие шага уже не выполнено (у кадров нет данных сцены и т.п.), подтверждать
        нечего: сразу показываем карточку шага с причиной, кнопка «Подтвердить» на ней
        недоступна. Замечание критика остаётся в предупреждениях. True — карточка открыта.

        Карточку шага не удалось собрать (сбой оценки в мосте, ``preview_failed``) — это не
        отказ условий: остаётся карточка критика. Если в плане есть и другие шаги со своим
        подтверждением, карточка говорит, что они идут дальше, каждый со своей карточкой.
        """
        goal = str((self.tasks.get(task_id) or {}).get("goal") or "")
        gated = [step for step in plan if self._step_needs_separate_approval(step)]
        for at, step in enumerate(plan):
            if not self._step_needs_separate_approval(step):
                continue
            try:
                card = await self._card_extra(task_id, step, goal)
            except Exception:  # noqa: BLE001 - карточку шага не собрать: остаётся карточка критика
                logger.opt(exception=True).warning("orch task {} blocked-step probe failed", task_id)
                return False
            if card.get("preview_failed"):
                logger.info(
                    "orch task {} step {} card failed to build: critic card kept", task_id, step.action
                )
                return False
            if not card.get("blocked") or not card.get("preview_errors"):
                continue
            risk, gate = self._step_gate(step)
            await self._checkpoint(task_id, session_id, plan, at)
            self._grant_target.pop(task_id, None)
            extra = {**card, "action": step.action, "risk": risk.value, "plan_risk": step.risk}
            warnings = [*(card.get("warnings") or [])]
            if note:
                warnings.append(note)
            if len(gated) > 1:
                warnings.append(_OTHER_GATED_STEPS.format(n=len(gated) - 1))
            if warnings:
                extra["warnings"] = warnings
            if risk in APPROVAL_RISKS:
                reason = "risk"
            else:
                reason = "paid" if gate == "paid" else "destructive"
                extra["gate"] = gate
                self._block_reason[task_id] = reason
            logger.info(
                "orch task {} step {} blocked by its preconditions: card shown instead of critic's",
                task_id,
                step.action,
            )
            await self._escalate(task_id, session_id, reason, extra=extra)
            return True
        return False

    def _spawn_drive(self, task_id: int, session_id: str, goal: str) -> None:
        self._cancel.discard(session_id)
        self._pause.discard(session_id)
        self._kill.discard(session_id)
        self._steer.pop(session_id, None)
        self._approval.pop(session_id, None)
        self._session_task[session_id] = task_id
        self._change_ids.setdefault(task_id, [])
        self._context.setdefault(task_id, "")
        self._paid_done.setdefault(task_id, set())
        self._runs[task_id] = self._watch_run(
            task_id,
            session_id,
            asyncio.create_task(
                self._drive(task_id, session_id, goal),
                name=f"orch-recover-{task_id}",
            ),
        )

    def _checkpoint_from_row(self, row: dict[str, Any]) -> CheckpointData | None:
        raw = row.get("plan_json")
        if not raw:
            return None
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return None
        if not isinstance(payload, list) or not payload:
            return None
        return CheckpointData(
            plan=[PlanStep.model_validate(item) for item in payload],
            position=int(row.get("position") or 0),
            spend=SpendSnapshot(
                internal=float(row.get("spend_internal") or 0),
                actions=float(row.get("spend_actions") or 0),
            ),
        )

    def _tracked_ids(self, session_id: str) -> set[int]:
        tracked = {task_id for task_id, _goal in self._queued.get(session_id, [])}
        tracked.update(task_id for task_id, job in self._runs.items() if not job.done())
        active = self._session_task.get(session_id)
        if active is not None:
            tracked.add(active)
        return tracked

    def _merge_queued(
        self,
        session_id: str,
        pairs: list[tuple[int, str]],
    ) -> list[tuple[int, str]]:
        merged = list(self._queued.get(session_id, []))
        seen = {task_id for task_id, _goal in merged}
        for task_id, goal in pairs:
            if task_id in seen or task_id in self._runs:
                continue
            merged.append((task_id, goal))
            seen.add(task_id)
        return merged

    def _paid_outcome_unknown(self, task_id: int, step: PlanStep) -> bool:
        """Платная строка ``running``: шаг уже стартовал, результат не записан."""
        return self._running_paid_row(task_id, step) is not None

    def _running_paid_row(self, task_id: int, step: PlanStep) -> dict[str, Any] | None:
        """Открытая платная строка ``running`` того же действия, params не важны."""
        if not step.paid:
            return None
        for row in self.tasks.list_steps(task_id):
            if str(row.get("status")) != "running" or not row.get("paid"):
                continue
            if str(row.get("action_name") or "") == step.action:
                return row
        return None

    def _retry_grant_key(self, step: PlanStep) -> str:
        """Действие + публичные params + признак retry. Одноразовый ключ grant."""
        return paid_step_identity(step.action, step.params) + "\nretry"

    async def _checkpoint_step(self, task_id: int) -> tuple[CheckpointData, PlanStep]:
        data = await asyncio.to_thread(self.checkpoints.load_latest, task_id)
        if data is None:
            raise LookupError(f"нет чекпоинта для задачи {task_id}")
        located = self._locate_paid_unknown(task_id, data)
        if located is not None:
            index, step = located
            if int(data.position) != index:
                data = data.model_copy(update={"position": index})
            return data, step
        if data.position < 0 or data.position >= len(data.plan):
            raise LookupError(f"нет шага чекпоинта для задачи {task_id}")
        return data, data.plan[int(data.position)]

    def _locate_paid_unknown(self, task_id: int, data: CheckpointData) -> tuple[int, PlanStep] | None:
        """Позиция и identity unknown-шага, а не начало батча.

        Живая строка ``running`` важнее памяти ``_unknown_step``: та
        остаётся на старом индексе, когда чекпоинт уже сдвинулся.
        """
        running = self._running_paid_index(task_id, data.plan)
        if running is not None:
            return running
        stored = _unpack_unknown(data.context or "")
        if isinstance(stored, dict) and "position" in stored:
            try:
                index = int(stored["position"])
            except (TypeError, ValueError):
                index = -1
            action = str(stored.get("action") or "")
            if 0 <= index < len(data.plan) and data.plan[index].action == action:
                return index, data.plan[index]
        return None

    def _grouped_unknown(
        self,
        plan: list[PlanStep],
        index: int,
        task_id: int,
    ) -> tuple[int, PlanStep] | None:
        """Unknown в той же parallel_group, включая платный шаг после read-only.

        Шаг вне группы (verify перед run_node) сюда не попадает и выполняется.
        """
        if index < 0 or index >= len(plan):
            return None
        group = plan[index].parallel_group
        last = index
        if group:
            cursor = index + 1
            while cursor < len(plan) and plan[cursor].parallel_group == group:
                last = cursor
                cursor += 1
        for cursor in range(index, last + 1):
            step = plan[cursor]
            if step.paid and self._paid_outcome_unknown(task_id, step):
                return cursor, step
        return None

    def _running_paid_index(self, task_id: int, plan: list[PlanStep]) -> tuple[int, PlanStep] | None:
        """Индекс плана для открытой платной строки. Публичные params точнее имени."""
        for row in self.tasks.list_steps(task_id):
            if str(row.get("status")) != "running" or not row.get("paid"):
                continue
            try:
                stored = json.loads(row.get("params_json") or "{}")
            except json.JSONDecodeError:
                stored = {}
            if not isinstance(stored, dict):
                stored = {}
            public = _public_params(stored)
            action = str(row.get("action_name") or "")
            same_name: tuple[int, PlanStep] | None = None
            for index, step in enumerate(plan):
                if not step.paid or step.action != action:
                    continue
                if _public_params(step.params) == public:
                    return index, step
                if same_name is None:
                    same_name = (index, step)
            if same_name is not None:
                return same_name
        return None

    def _remember_unknown_step(self, task_id: int, index: int, step: PlanStep) -> None:
        self._unknown_step[task_id] = {
            "position": index,
            "action": step.action,
            "params": _public_params(step.params),
        }

    async def _continue_from_checkpoint(self, task_id: int, session_id: str, data: CheckpointData) -> None:
        self._arm_resume(session_id, task_id, data)
        await self._set_status(task_id, TaskStatus.executing, session_id=session_id)
        goal = str((self.tasks.get(task_id) or {}).get("goal") or "")
        self._runs[task_id] = self._watch_run(
            task_id,
            session_id,
            asyncio.create_task(
                self._execute_plan(task_id, session_id, list(data.plan), int(data.position), goal),
                name=f"orch-paid-unknown-{task_id}",
            ),
        )

    def _count_unknown_attempt(
        self,
        task_id: int,
        step: PlanStep,
        row: dict[str, Any],
        status: str,
    ) -> float:
        """Исход неизвестен: в строку и spend пишется прогноз, с пометкой оценки."""
        cost = forecast_cost([step])
        self.tasks.finish_step(int(row["id"]), status, None, cost)
        if cost:
            self.tasks.add_spend(task_id, actions=cost)
        note = f"estimate {step.action} {cost}"
        prev, _packed = _split_appr_context(self._context.get(task_id, ""))
        visible = _strip_unknown(prev).split(_BLOCK_MARK, 1)[0]
        if note not in visible:
            self._context[task_id] = f"{note}\n{prev}".strip() if prev else note
        return cost

    async def _stamp_estimate(
        self,
        task_id: int,
        session_id: str,
        data: CheckpointData,
    ) -> CheckpointData:
        raw_context = self._context.get(task_id, "")
        if not raw_context:
            stored, _packed = _split_appr_context(data.context or "")
            raw_context, _mark = _unpack_approved(stored)
        stamped = data.model_copy(
            update={
                "spend": self._spend(task_id),
                "context": self._context_with_appr(task_id, raw_context),
            }
        )
        await write_on_loop(
            self.tasks.mirror_progress,
            task_id,
            list(stamped.plan),
            int(stamped.position),
            stamped.spend,
        )
        await asyncio.to_thread(self.checkpoints.save, task_id, session_id, stamped)
        return stamped

    async def _accept_partial_write(self, task_id: int, session_id: str) -> None:
        """Принять partial: строка done с тем же change_id, execute этого шага нет.

        Пока inner write ещё идёт, продолжение плана ждёт его конца.
        """
        inflight = self._inflight_write.get(int(task_id))
        if inflight is not None and not inflight.done():
            try:
                await inflight
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("orch partial accept: запись задачи {} завершилась ошибкой", task_id)
        data, step = await self._checkpoint_step(task_id)
        row = self._open_partial_row(task_id, step, list(data.plan), int(data.position))
        if row is not None:
            raw = row.get("change_id")
            change_id = str(raw) if raw else None
            await write_on_loop(
                self.tasks.finish_step,
                int(row["id"]),
                "done",
                change_id,
                float(row.get("cost") or 0),
            )
        self._block_reason.pop(task_id, None)
        advanced = data.model_copy(update={"position": int(data.position) + 1})
        await self._continue_from_checkpoint(task_id, session_id, advanced)

    async def _deny_partial_write(self, task_id: int, session_id: str) -> None:
        """Отказ partial: задача failed, строка и change_id остаются."""
        _data, step = await self._checkpoint_step(task_id)
        self._block_reason.pop(task_id, None)
        await self._set_status(task_id, TaskStatus.failed, session_id=session_id, error="partial_write")
        await self.bus.publish(
            session_id,
            "task_failed",
            {"error": "partial_write", "action": step.action},
            task_id=task_id,
        )

    async def _approve_paid_unknown(self, task_id: int, session_id: str) -> None:
        """«Шаг выполнен, продолжить»: done и _paid_done, execute этого шага нет."""
        self._retry_armed.pop(task_id, None)
        self._paid_outcome_hold.discard(task_id)
        data, step = await self._checkpoint_step(task_id)
        row = self._running_paid_row(task_id, step)
        if row is not None and row.get("change_id"):
            text = str(row["change_id"])
            await write_on_loop(
                self.tasks.finish_step,
                int(row["id"]),
                "done",
                text,
                float(row.get("cost") or 0),
            )
            bucket = self._change_ids.setdefault(task_id, [])
            if text not in bucket:
                bucket.append(text)
        elif row is not None:
            self._count_unknown_attempt(task_id, step, row, "done")
        self._paid_done.setdefault(task_id, set()).add(paid_step_identity(step.action, step.params))
        self._block_reason.pop(task_id, None)
        self._drop_block_text(task_id)
        data = await self._stamp_estimate(task_id, session_id, data)
        advanced = int(data.position) + 1
        if self._paid_spend_fills_budget(task_id):
            self._budget_stop.add(task_id)
            self._block_reason[task_id] = ApprovalReason.budget_exhausted.value
            await self._checkpoint(task_id, session_id, list(data.plan), advanced)
            await self._replace_waiting_reason(
                task_id,
                session_id,
                ApprovalReason.budget_exhausted.value,
            )
            return
        await self._continue_from_checkpoint(
            task_id,
            session_id,
            data.model_copy(update={"position": advanced}),
        )

    async def _deny_paid_unknown(self, task_id: int, session_id: str) -> None:
        """«Остановить задачу»: шаг и задача failed, execute не вызывается."""
        self._retry_armed.pop(task_id, None)
        data, step = await self._checkpoint_step(task_id)
        row = self._running_paid_row(task_id, step)
        if row is not None:
            self._count_unknown_attempt(task_id, step, row, "failed")
        await self._stamp_estimate(task_id, session_id, data)
        self._block_reason.pop(task_id, None)
        await self._set_status(
            task_id, TaskStatus.failed, session_id=session_id, error="paid_outcome_unknown"
        )
        await self.bus.publish(
            session_id,
            "task_failed",
            {"error": "paid_outcome_unknown", "action": step.action},
            task_id=task_id,
        )

    async def _arm_paid_retry_forecast(self, task_id: int, session_id: str) -> dict[str, Any]:
        """«Перезапустить шаг»: прогноз, без execute. Grant вооружается отдельно."""
        data, step = await self._checkpoint_step(task_id)
        key = self._retry_grant_key(step)
        if (task_id, key) not in self._retry_spent:
            self._retry_armed[task_id] = key
        forecast = forecast_cost([step])
        armed = self._retry_armed.get(task_id) == key
        await self.publish_approval_required(
            session_id,
            {
                "reason": "paid_outcome_unknown",
                "task_id": task_id,
                "action": step.action,
                "position": int(data.position),
                "forecast_usd": forecast,
                "decisions": approval_decision_choices(),
                "retry_armed": armed,
            },
            task_id=task_id,
            reuse=True,
        )
        return {
            "executed": False,
            "decision": ApprovalDecision.retry_step.value,
            "forecast_usd": forecast,
            "retry_armed": armed,
        }

    def _retry_grant_is_spent(self, task_id: int) -> bool:
        """Ключ уже сожжён, и нового вооружения нет. Повтор не зависит от статуса."""
        key = int(task_id)
        if self._retry_armed.get(key) is not None:
            return False
        return any(stored == key for stored, _key in self._retry_spent)

    async def _consume_paid_retry_grant(self, task_id: int, session_id: str) -> dict[str, Any]:
        """Одноразовый retry grant. Без прогноза и повторно execute не зовётся.

        Пока задача в ``_budget_stop``, ключ не сгорает и шаг не стартует.
        """
        if self._retry_armed.get(task_id) is None and any(
            stored == task_id for stored, _key in self._retry_spent
        ):
            return {
                "executed": False,
                "reason": "retry_grant_spent",
                "forecast_usd": None,
                "retry_armed": False,
            }
        if task_id not in self._retry_armed:
            return {
                "executed": False,
                "reason": "retry_grant_required",
                "forecast_usd": None,
                "retry_armed": False,
            }
        data, step = await self._checkpoint_step(task_id)
        key = self._retry_grant_key(step)
        forecast = forecast_cost([step])
        if (task_id, key) in self._retry_spent or self._retry_armed.get(task_id) != key:
            reason = "retry_grant_spent" if (task_id, key) in self._retry_spent else "retry_grant_required"
            return {
                "executed": False,
                "reason": reason,
                "forecast_usd": forecast,
                "retry_armed": False,
            }
        if task_id in self._budget_stop:
            await self.publish_approval_required(
                session_id,
                {
                    "reason": ApprovalReason.budget_exhausted.value,
                    "task_id": task_id,
                    "action": step.action,
                    "forecast_usd": forecast,
                },
                task_id=task_id,
                reuse=True,
            )
            return {
                "executed": False,
                "reason": ApprovalReason.budget_exhausted.value,
                "forecast_usd": forecast,
                "retry_armed": self._retry_armed.get(task_id) == key,
            }
        self._retry_spent.add((task_id, key))
        self._retry_armed.pop(task_id, None)
        self._paid_outcome_hold.discard(task_id)
        row = self._running_paid_row(task_id, step)
        if row is not None:
            self._count_unknown_attempt(task_id, step, row, "cancelled")
        self._block_reason.pop(task_id, None)
        self._drop_block_text(task_id)
        data = await self._stamp_estimate(task_id, session_id, data)
        self._risk_granted.add(self._risk_grant_key(task_id, int(data.position), step))
        await self._continue_from_checkpoint(task_id, session_id, data)
        return {
            "executed": True,
            "decision": ApprovalDecision.retry_step.value,
            "forecast_usd": forecast,
            "retry_armed": False,
        }

    def _bind_partial_llm_cost(self) -> None:
        """Частичная стоимость LLM живёт на задаче, не на общем роутере."""
        seen: set[int] = set()
        owners = (
            self.planner,
            getattr(self.planner, "router", None),
            self.critic,
            getattr(self.critic, "router", None),
        )
        for owner in owners:
            if owner is None or id(owner) in seen:
                continue
            seen.add(id(owner))
            original = getattr(owner, "call", None)
            if not callable(original) or getattr(owner, "_orch_partial_bound", False):
                continue
            owner.call = self._partial_cost_call(owner, original)
            owner._orch_partial_bound = True
            self._install_partial_meter(owner)

    def _install_partial_meter(self, owner: Any) -> None:
        """Присваивание partial_cost_usd сразу плюсуется задаче из contextvar.

        Общий атрибут больше не единственная копилка: два call не затирают
        суммы. Ноль (сброс после снятия) в копилку не идёт.
        """
        if getattr(owner, "_orch_meter", False) or not hasattr(owner, "partial_cost_usd"):
            return
        try:
            visible = float(getattr(owner, "partial_cost_usd", 0) or 0)
        except (TypeError, ValueError):
            visible = 0.0
        with contextlib.suppress(AttributeError):
            delattr(owner, "partial_cost_usd")
        supervisor = self

        class _Metered(owner.__class__):
            @property
            def partial_cost_usd(self) -> float:
                return float(getattr(self, "_orch_visible_partial", 0) or 0)

            @partial_cost_usd.setter
            def partial_cost_usd(self, value: Any) -> None:
                try:
                    number = float(value or 0)
                except (TypeError, ValueError):
                    number = 0.0
                self._orch_visible_partial = number
                if number == 0:
                    return
                slot = _LLM_SLOT.get()
                if not slot or slot.get("task") is None:
                    return
                task_key = int(slot["task"])
                slot["credit"] = float(slot.get("credit") or 0) + number
                supervisor._partial_llm_cost[task_key] = (
                    supervisor._partial_llm_cost.get(task_key, 0.0) + number
                )

        owner.__class__ = _Metered
        owner._orch_visible_partial = visible
        owner._orch_meter = True

    def _partial_cost_call(self, owner: Any, original: Any) -> Any:
        async def _call(*args: Any, **kwargs: Any) -> Any:
            raw_task = kwargs.get("task_id")
            try:
                task_key = int(raw_task) if raw_task not in (None, "") else None
            except (TypeError, ValueError):
                task_key = None
            if task_key is None:
                task_key = _LLM_TASK.get()
            slot = {"credit": 0.0, "task": task_key}
            token = _LLM_SLOT.set(slot)
            try:
                result = await original(*args, **kwargs)
            except asyncio.CancelledError:
                self._take_partial_cost(owner)
                if task_key is not None:
                    visible = _router_visible_cost(owner)
                    current = self._partial_llm_cost.get(task_key, 0.0)
                    if visible > current:
                        self._partial_llm_cost[task_key] = visible
                raise
            else:
                if task_key is not None and slot["credit"]:
                    left = self._partial_llm_cost.get(task_key, 0.0) - float(slot["credit"])
                    if left > 0:
                        self._partial_llm_cost[task_key] = left
                    else:
                        self._partial_llm_cost.pop(task_key, None)
                self._take_partial_cost(owner)
                return result
            finally:
                _LLM_SLOT.reset(token)

        return _call

    def _take_partial_cost(self, owner: Any) -> float:
        raw = getattr(owner, "partial_cost_usd", None)
        try:
            value = float(raw or 0)
        except (TypeError, ValueError):
            value = 0.0
        if hasattr(owner, "partial_cost_usd"):
            owner.partial_cost_usd = 0.0
        return value if value > 0 else 0.0

    def _charge_cancelled_llm(self, task_id: int) -> None:
        cost = self._partial_llm_cost.pop(int(task_id), 0.0)
        if cost:
            self.tasks.add_spend(task_id, internal=cost)

    def _llm_kwargs(self, task_id: int, role: str) -> dict[str, Any]:
        return {
            "run_id": orch_run_id(task_id=task_id),
            "task_id": task_id,
            "agent_role": role,
            "budget_scope": BudgetScope.internal.value,
            "reasoning_effort": ORCH_REASONING_EFFORT,
        }

    def _spawn(self, awaitable: Any) -> asyncio.Task[Any]:
        job: asyncio.Task[Any] = asyncio.get_running_loop().create_task(awaitable)
        self._shield_jobs.add(job)
        job.add_done_callback(self._shield_jobs.discard)
        return job

    async def _flush_kill_publishes(self) -> None:
        pending = list(self._pending_kill_publish)
        self._pending_kill_publish.clear()
        for session_id, task_id in pending:
            self._spawn(self._publish_kill(session_id, task_id))
        queued = list(self._pending_queue_cancel)
        self._pending_queue_cancel.clear()
        for session_id, task_id in queued:
            self._spawn(self._cancel_queued(session_id, task_id))
        pending_paid = list(self._pending_paid_unknown)
        self._pending_paid_unknown.clear()
        for session_id, task_id in pending_paid:
            self._spawn(self._mark_paid_outcome_unknown(session_id, task_id))

    async def _publish_kill(self, session_id: str, task_id: int | None) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "stop_key_set": False,
            "stop_key": "pending",
            "only_node": True,
            "task_id": task_id,
        }
        projects = self._kill_projects(task_id) if task_id is not None else []
        if projects:
            from app.services.step_cancel import is_stop_requested, stop_flag_path

            for project_id in projects:
                self._note_kill_stop(project_id, task_id)
            own = self._task_project_id(task_id) if task_id is not None else None
            foreign = {project_id for project_id in projects if project_id != own}
            if foreign and task_id is not None:
                self._kill_stopped.setdefault(int(task_id), set()).update(foreign)
            payload["stop_key_set"] = all(is_stop_requested(project_id) for project_id in projects)
            payload["stop_key"] = stop_flag_path(projects[0]).name
            if len(projects) > 1:
                payload["stop_keys"] = [stop_flag_path(project_id).name for project_id in projects]
        queue = await self._dequeue_session_queue(session_id, task_id)
        payload["queue_removed"] = queue["removed"]
        payload["queue_stopped"] = queue["stopping"]
        payload["queue_removed_count"] = len(queue["removed"])
        await self.bus.publish(session_id, "kill_switch", payload, task_id=task_id)
        return payload

    def _session_queue_projects(self, session_id: str) -> list[int]:
        """Проекты, которые задачи этой сессии ставили в очередь (шаги ``queue.enqueue_many``).

        Берутся все задачи сессии, и закрытые, и после рестарта: kill идёт после
        другой задачи, а проекты первой всё ещё ждут в очереди. Это только кандидаты:
        снимаются лишь номера с меткой этой сессии (см. ``dequeue_chat_projects``),
        так что шаг, который не прошёл или упал на середине, лишнего не снимет.
        """
        from app.services.orch_tenancy import session_tenant
        from app.services.tenant import tenant_scope

        found: list[int] = []
        try:
            with tenant_scope(session_tenant(session_id)):
                rows = self.tasks.session_steps(session_id, "queue.enqueue_many")
        except Exception:
            logger.exception("orch kill: queue steps of session {} not read", session_id)
            rows = []
        for row in rows:
            try:
                params = json.loads(row.get("params_json") or "{}")
            except (TypeError, ValueError):
                params = {}
            raw = params.get("project_ids") if isinstance(params, dict) else None
            for item in raw if isinstance(raw, list) else []:
                try:
                    value = int(item)
                except (TypeError, ValueError):
                    continue
                if value > 0 and value not in found:
                    found.append(value)
        return found

    async def _dequeue_session_queue(self, session_id: str, task_id: int | None) -> dict[str, list[int]]:
        """Kill: проекты, которые чат этой сессии ставил в очередь генерации, оттуда убираются.

        Иначе они стартовали бы позже, уже после «аварийного стопа». Ждущие получают
        user_stop; у тех, где шаг уже идёт, ставится stop-ключ kill: воркер снимет шаг и
        дальше проект не пойдёт. Номер, который поставил человек, ручной стоп человека,
        чужие сессии и общий стоп очереди не трогаются.
        """
        empty: dict[str, list[int]] = {"removed": [], "stopping": []}
        projects = self._session_queue_projects(session_id)
        if not projects:
            return empty
        since = time.time()
        try:
            got = await _dequeue_chat_from_queue(projects, session_id)
        except Exception:
            logger.exception("orch kill: queue of session {} not cleared", session_id)
            return empty
        removed = [int(item) for item in (got or {}).get("removed") or []]
        stopping = [int(item) for item in (got or {}).get("stopping") or [] if int(item) in removed]
        for project_id in stopping:
            self._hold_queue_stop(project_id, task_id)
        waiting = [project_id for project_id in removed if project_id not in stopping]
        if waiting:
            stopping.extend(await self._recheck_queue_stop(waiting, task_id, 0.0, session_id, since))
            self._spawn(
                self._recheck_queue_stop(waiting, task_id, QUEUE_RECHECK_DELAY_SEC, session_id, since)
            )
        return {"removed": removed, "stopping": stopping}

    def _hold_queue_stop(self, project_id: int, task_id: int | None) -> None:
        self._note_kill_stop(project_id, task_id)
        if task_id is not None:
            self._kill_stopped.setdefault(int(task_id), set()).add(int(project_id))

    async def _recheck_queue_stop(
        self, project_ids: list[int], task_id: int | None, delay: float, session_id: str, since: float
    ) -> list[int]:
        """Снятый стопом проект всё же стартовал (тик очереди шёл в тот же момент): stop-ключ.

        Проект, который человек после стопа снова поставил в очередь или запустил ▶, не трогаем.
        """
        if delay > 0:
            await asyncio.sleep(delay)
        try:
            busy = [int(item) for item in await _recheck_dequeued_in_db(project_ids, session_id, since) or []]
        except Exception:
            logger.exception("orch kill: queue recheck of {} failed", project_ids)
            return []
        for project_id in busy:
            logger.warning("orch kill: #{} стартовал во время стопа — ставим stop-ключ", project_id)
            self._hold_queue_stop(project_id, task_id)
        return busy

    def _kill_projects(self, task_id: int) -> list[int]:
        """Проект задачи и проекты её шагов, которые ещё идут (запуск сразу в нескольких проектах).

        Без них kill задачи «запусти проекты 5..10» не ставил ни одного stop-ключа:
        у такой задачи нет одного ``project_id``, и генерация во всех шести шла дальше.
        Проекты шагов, которые ещё не стартовали, не трогаем: kill их и так не запустит.
        """
        found: list[int] = []
        own = self._task_project_id(task_id)
        if own is not None:
            found.append(own)
        for value in self._step_projects(task_id, running_only=True):
            if value not in found:
                found.append(value)
        return self._visible_to_task(task_id, found, keep=own)

    def _visible_to_task(self, task_id: int, project_ids: list[int], *, keep: int | None) -> list[int]:
        """Только проекты, которые видит арендатор задачи (RLS), — до ключа стопа.

        Ключ стопа — файл и отмена задач процесса, RLS его не закрывает: номер
        проекта в параметрах шага мог указывать на чужой проект. Владелец
        (арендатора нет) — прежнее поведение. Сбой чтения — проект самой
        задачи остаётся, номера из шагов отбрасываются. Строку задачи не видно
        (чужой арендатор) — тоже только проект самой задачи.
        """
        if not project_ids:
            return project_ids
        try:
            row = self.tasks.get(task_id)
        except Exception:
            logger.exception("orch kill: task {} not read", task_id)
            row = None
        if row is None:
            return [p for p in project_ids if p == keep]
        tenant = row.get("tenant_id")
        if tenant is None or str(tenant).strip() == "":
            return project_ids
        from sqlalchemy import bindparam, text

        from app.orchestrator.db.engine import get_engine
        from app.services.tenant import tenant_scope

        query = text("SELECT id FROM projects WHERE id IN :ids").bindparams(bindparam("ids", expanding=True))
        try:
            with tenant_scope(str(tenant)), get_engine().connect() as conn:
                visible = {int(r[0]) for r in conn.execute(query, {"ids": [int(p) for p in project_ids]})}
        except Exception:
            logger.exception("orch kill: projects of task {} not checked", task_id)
            visible = {keep} if keep is not None else set()
        skipped = [p for p in project_ids if p not in visible]
        if skipped:
            logger.warning("orch kill: task {} — чужие проекты {} пропущены", task_id, skipped)
        return [p for p in project_ids if p in visible]

    def _step_projects(self, task_id: int, *, running_only: bool) -> list[int]:
        """Проекты платных или изменяющих шагов задачи (только идущих, если ``running_only``)."""
        found: list[int] = []
        try:
            rows = self.tasks.list_steps(task_id)
        except Exception:
            logger.exception("orch kill: steps of task {} not read", task_id)
            rows = []
        for row in rows:
            if not (row.get("paid") or row.get("mutating")):
                continue
            if running_only and str(row.get("status")) != "running":
                continue
            try:
                params = json.loads(row.get("params_json") or "{}")
                value = int(params.get("project_id") or 0) if isinstance(params, dict) else 0
            except (TypeError, ValueError):
                value = 0
            if value > 0 and value not in found:
                found.append(value)
        return found

    async def _drive(self, task_id: int, session_id: str, goal: str) -> None:
        from app.orchestrator.routing.classifier import is_control_request
        from app.services import spend_guard

        # Стоп, пауза и статус доходят до планёра и при сработавшем потолке трат.
        with spend_guard.chat_control(is_control_request(goal)):
            await self._drive_task(task_id, session_id, goal)

    async def _drive_task(self, task_id: int, session_id: str, goal: str) -> None:
        try:
            plan = await self._plan_and_review(task_id, session_id, goal, self._context.get(task_id, ""))
            if plan is None:
                return
            await self._set_status(task_id, TaskStatus.executing, session_id=session_id)
            await self._execute_plan(task_id, session_id, plan, 0, goal)
        except asyncio.CancelledError:
            self._charge_cancelled_llm(task_id)
            if task_id in self._paid_open or task_id in self._shield_orphaned:
                if asyncio.current_task() and asyncio.current_task().cancelling():
                    raise
                return
            current = self._status(task_id)
            if current not in TERMINAL_STATUSES:
                await self._set_status(task_id, TaskStatus.cancelled, session_id=session_id)
            if asyncio.current_task() and asyncio.current_task().cancelling():
                raise
        except Exception as exc:
            logger.exception("orch task {} failed", task_id)
            await self._settle_failure(task_id, session_id, exc)

    async def _plan_and_review(
        self,
        task_id: int,
        session_id: str,
        goal: str,
        context: str,
    ) -> list[PlanStep] | None:
        started = _monotonic()
        plan = await self._make_plan(task_id, session_id, goal, context)
        if plan is None:
            return None
        revises = 0
        while True:
            blocked = await self._screen_plan(task_id, session_id, plan, revise=revises < 2)
            if plan and blocked:
                why = blocked if isinstance(blocked, str) else str(self._block_reason.get(task_id) or "")
                self._trace_plan(task_id, "screen_rejected", plan, why)
            if isinstance(blocked, str):
                revises += 1
                context = (context + "\n" + blocked).strip()
                self._context[task_id] = context
                plan = await self._make_plan(task_id, session_id, goal, context)
                if plan is None:
                    return None
                continue
            if blocked:
                return None
            await self._set_status(task_id, TaskStatus.critic_review, session_id=session_id)
            if self._redo_card_due(task_id, plan):
                await self._escalate_plan(task_id, session_id, plan, _PLAN_CHANGED)
                return None
            verdict = await self._review_plan(task_id, session_id, plan, context)
            if verdict is None:
                return None
            if verdict == "approve":
                self._approved[task_id] = _plan_fingerprint(plan)
                return plan
            self._trace_plan(
                task_id, f"critic_{verdict}", plan, str(self._critic_last(task_id, "last_reason") or "")
            )
            if verdict in {"reject", "escalate"}:
                await self._critic_block(task_id, session_id, plan, verdict)
                return None
            revises += 1
            if revises > 2:
                await self._critic_revised_out(task_id, session_id, plan)
                return None
            if _monotonic() - started > REVISE_TIME_BUDGET_S:
                logger.info(
                    "orch task {} critic revise after {:.0f}s: card instead of a new round",
                    task_id,
                    _monotonic() - started,
                )
                await self._critic_revised_out(task_id, session_id, plan, slow=True)
                return None
            context = (context + "\n" + self._critic_feedback(task_id, verdict)).strip()
            self._context[task_id] = context
            plan = await self._make_plan(task_id, session_id, goal, context)
            if plan is None:
                return None

    def _last_diag_project(self, session_id: str) -> int | None:
        # «А можешь найти саму причину?» без номера — тот же проект, что в прошлом разборе сбоя.
        try:
            steps = self.tasks.session_steps(session_id, "diag.node_failure")
        except Exception:
            return None
        for row in reversed(steps):
            try:
                pid = json.loads(row.get("params_json") or "{}").get("project_id")
            except (TypeError, ValueError):
                continue
            if isinstance(pid, int) and pid > 0:
                return pid
        return None

    async def _make_plan(
        self,
        task_id: int,
        session_id: str,
        goal: str,
        context: str,
    ) -> list[PlanStep] | None:
        if task_id in self._budget_stop:
            await self._settle_stopped(task_id, session_id)
            return None
        if self._status(task_id) is not TaskStatus.planning:
            await self._set_status(task_id, TaskStatus.planning, session_id=session_id)
        token = _LLM_TASK.set(int(task_id))
        try:
            drafted = await self._draft_plan(task_id, session_id, goal, context)
            if drafted == [] and self._asks_approval(task_id):
                self._trace_plan(task_id, "asked_approval", [], self._planner_reason(task_id))
                drafted = await self._draft_plan(
                    task_id, session_id, goal, (context + "\n" + _APPROVAL_NOTE).strip()
                )
            if drafted and _enable_not_launch(goal, list(drafted)):
                self._trace_plan(task_id, "enable_not_launch", list(drafted), "включение ноды, не запуск")
                drafted = await self._draft_plan(
                    task_id, session_id, goal, (context + "\n" + _ENABLE_NOTE).strip()
                )
                if drafted and _enable_not_launch(goal, list(drafted)):
                    drafted = [step for step in drafted if _step_action(step) != "step.run_step"]
        finally:
            _LLM_TASK.reset(token)
        if drafted is None:
            return None
        # Правка настроек проекта — только названные ключи, до сверки с текущими значениями.
        drafted = scope_settings(list(drafted), goal)
        # «Кадр 8: ночное освещение» — свет на доске, а не платная перерисовка картинки.
        drafted = light_as_coverage(drafted, goal)
        # «Музыка для проекта 4 — файл C:\...\bgm.wav»: путь из просьбы, если шаг загрузки без файла.
        drafted = upload_source_from_goal(drafted, goal)
        # «Найди причину ошибки в ИИ-редакторе сцен»: разбор сбоя ноды, а не одно чтение доски.
        drafted = failure_reads(drafted, goal, None, self._last_diag_project(session_id))
        # «Запусти проекты 3..12 одновременно» — очередь генерации, а не шаг в каждом проекте.
        # Участнику окно параллели (одно на всю студию) не ставим: вместо шага — строка карточки.
        launch_notes: list[str] = []
        drafted = route_multi_launch(drafted, goal, _queue_limit(), self._launch_allowed, launch_notes)
        if launch_notes:
            self._launch_note[task_id] = " ".join(launch_notes)
        else:
            self._launch_note.pop(task_id, None)
        try:
            resolved = _chain_from_queue(
                [await self._resolved(self._with_defaults(step)) for step in drafted], goal
            )
        except PlanResolveRefused as exc:
            # Шаг нельзя достроить без чтения данных: плана нет, человеку — текст отказа.
            logger.warning("orch task {} plan refused: {}", task_id, exc)
            await write_on_loop(self._save_plan, task_id, [])
            self._code_plans.pop(task_id, None)
            self._from_code.pop(task_id, False)
            self._trace_plan(task_id, "plan_refused", [], str(exc))
            self._fail_detail[task_id] = str(exc)
            self._code_answers.add(task_id)
            return []
        plan = [
            tighten_paid_step(self.actions, step_with_registry_paid(step, self.actions)) for step in resolved
        ]
        plan, nothing = await self._drop_unchanged_settings(task_id, plan, goal)
        plan = await self._drop_blocked_launches(task_id, plan, goal, session_id)
        if nothing:
            # «В проекте #5 модель картинок уже GPT Image 2, менять нечего»: ответ без карточки.
            await write_on_loop(self._save_plan, task_id, [])
            self._fail_detail[task_id] = nothing
            self._code_answers.add(task_id)
            self._trace_plan(task_id, "settings_unchanged", [], nothing)
            return []
        await write_on_loop(self._save_plan, task_id, plan)
        from_code = self._from_code.pop(task_id, False)
        if from_code and plan:
            self._code_plans[task_id] = _plan_fingerprint(plan)
        else:
            self._code_plans.pop(task_id, None)
        logger.info("orch task {} planner: {} steps", task_id, len(plan))
        if not plan:
            self._trace_plan(task_id, "empty_plan", plan, self._planner_reason(task_id))
            kind = await self._unknown_model_kind(task_id)
            self._note_empty_reason(task_id, from_code=from_code, model_kind=kind)
        if await self._absorb_budget(task_id, session_id, self._planner_last(task_id, "last_response")):
            return None
        return plan

    async def _drop_unchanged_settings(
        self, task_id: int, plan: list[PlanStep], goal: str = ""
    ) -> tuple[list[PlanStep], str]:
        """Правки настроек, где всё уже так, как просят, из плана убираются.

        «Поставь модель по умолчанию» сверяется с пустым полем проекта как с умолчанием и
        пишет «→ по умолчанию (…)»; явная модель («смени на gpt») — строго, без «по умолчанию».

        Возвращает план и ответ человеку, если менять нечего совсем. Пропущенные проекты
        и «было → станет» остальных карточка покажет словами (``_settings_cards``).
        """
        probe = getattr(self.actions, "settings_diff", None)
        self._settings_text.pop(task_id, None)
        self._settings_skipped.pop(task_id, None)
        if not callable(probe) or not any(step.action in _SETTINGS_ACTIONS for step in plan):
            return plan, ""
        kept: list[PlanStep] = []
        texts: dict[str, str] = {}
        skipped: list[tuple[int | None, str]] = []
        tail = "_default" if _ASKS_DEFAULT.search(str(goal or "").lower()) else ""
        for step in plan:
            if step.action not in _SETTINGS_ACTIONS:
                kept.append(step)
                continue
            try:
                found = await probe(step.action, dict(step.params))
            except Exception:
                logger.exception("orch settings diff failed for task {}", task_id)
                found = None
            if isinstance(found, dict) and found.get(f"unchanged{tail}", found.get("unchanged")):
                skipped.append(
                    (_step_project(step), str(found.get(f"same{tail}_ru") or found.get("same_ru") or ""))
                )
                continue
            change = (
                found.get(f"change{tail}_ru") or found.get("change_ru") if isinstance(found, dict) else ""
            )
            if change:
                texts[paid_step_identity(step.action, step.params)] = str(change)
            kept.append(step)
        if texts:
            self._settings_text[task_id] = texts
        if not skipped:
            return plan, ""
        if kept:
            self._settings_skipped[task_id] = _skipped_text(skipped)
            return kept, ""
        return [], _nothing_text(skipped)

    def _settings_cards(self, task_id: int, steps: list[PlanStep], views: list[dict[str, Any]]) -> None:
        """Строка шага правки настроек: что именно поменяется, словами («модель картинок: A → B»)."""
        texts = self._settings_text.get(int(task_id)) or {}
        for step, view in zip(steps, views, strict=False):
            if step.action not in _SETTINGS_ACTIONS or not isinstance(view, dict):
                continue
            change = texts.get(paid_step_identity(step.action, step.params))
            if not change:
                continue
            pid = _step_project(step)
            view["params_text"] = change + (f"; проект #{pid}" if pid else "")
            view["text"] = f"{view.get('title') or ''} — {view['params_text']}"

    def _launch_allowed(self, name: str) -> bool:
        """Действие есть в каталоге чата и доступно личности задачи (участнику — без действий всей студии)."""
        if not_in_whitelist(name, self.actions):
            return False
        from app.services.action_layer.plan import member_hidden_actions

        return name not in member_hidden_actions()

    def _settings_card_note(self, task_id: int, body: dict[str, Any]) -> None:
        """«Не поставлены в очередь: #10 — …», «Окно параллели меняет только админ», «Пропущено
        4 проекта, менять там нечего» — первыми строками предупреждений карточки."""
        notes = [
            note
            for note in (
                self._launch_skipped.get(int(task_id)),
                self._launch_note.get(int(task_id)),
                self._settings_skipped.get(int(task_id)),
            )
            if note
        ]
        if not notes:
            return
        warnings = [w for w in body.get("warnings") or [] if w not in notes]
        body["warnings"] = [*notes, *warnings]

    async def _launch_blocked(
        self, probe: Any, task_id: int, step: PlanStep, session_id: str | None = None
    ) -> dict[int, str]:
        from app.services.orch_tenancy import session_tenant
        from app.services.tenant import current_tenant, tenant_scope

        # Проверка — под арендатором сессии чата: условия запуска видят только свои проекты.
        tenant = session_tenant(session_id) if session_id else current_tenant()
        try:
            with tenant_scope(tenant):
                found = await probe(step.action, dict(step.params))
        except Exception:
            logger.exception("orch launch blockers failed for task {}", task_id)
            return {}
        out: dict[int, str] = {}
        items = found.get("blocked") if isinstance(found, dict) else None
        for item in items if isinstance(items, list) else []:
            pid = item.get("project_id") if isinstance(item, dict) else None
            if isinstance(pid, int) and not isinstance(pid, bool):
                # Только готовая человеческая причина: сырой текст проверки в карточку не идёт.
                out[pid] = str(item.get("reason_ru") or "не выполнено условие запуска")
        return out

    async def _drop_blocked_launches(
        self, task_id: int, plan: list[PlanStep], goal: str = "", session_id: str | None = None
    ) -> list[PlanStep]:
        """Массовый запуск без проектов, которые упрутся в жёсткое условие запуска.

        «Запусти проекты 3..12»: проект без данных сцены не держит девять готовых — его номер
        уходит из шага до карточки, карточка первой строкой говорит, кого и почему не
        поставили и как запустить потом. Карточка и executor проверяют оставшихся как обычно,
        подтверждение человека остаётся. Если не проходит ни один проект или проект один —
        план не трогаем: карточка покажет отказ, как раньше.
        """
        self._launch_skipped.pop(task_id, None)
        probe = getattr(self.actions, "launch_blockers", None)
        if not callable(probe):
            return plan
        out = list(plan)
        notes: list[str] = []
        for at, step in enumerate(out):
            if step.action != "queue.enqueue_many":
                continue
            ids = [
                pid
                for pid in step.params.get("project_ids") or []
                if isinstance(pid, int) and not isinstance(pid, bool)
            ]
            if len(ids) < 2:
                continue
            blocked = await self._launch_blocked(probe, task_id, step, session_id)
            dropped = [(pid, blocked[pid]) for pid in dict.fromkeys(ids) if pid in blocked]
            kept = [pid for pid in ids if pid not in blocked]
            if not dropped or not kept:
                continue
            out[at] = step.model_copy(update={"params": {**step.params, "project_ids": kept}})
            # Окно параллели — по оставшимся проектам, тем же правилом, что при маршруте в очередь.
            out = refit_parallel(out, goal, _queue_limit(), len(set(kept)))
            # Участнику шага параллели нет вовсе (только админ): если оставшиеся проекты и так
            # влезают в окно, строка «окно меняет только админ» карточке больше не нужна.
            if task_id in self._launch_note and not parallel_needed(goal, _queue_limit(), len(set(kept))):
                self._launch_note.pop(task_id, None)
            notes.append(
                _blocked_launch_text(
                    ("Не поставлен в очередь", "Не поставлены в очередь"),
                    dropped,
                    kept,
                    ("ставится в очередь как обычно", "ставятся в очередь как обычно"),
                )
            )
        runs = [step for step in out if step.action == "step.run_step"]
        others = [step for step in out if step.action != "step.run_step"]
        projects = [_step_project(step) for step in runs]
        # Шаг на проект: только когда кроме запусков в плане одни чтения (сверка состояния).
        if (
            len(runs) >= 2
            and None not in projects
            and len(set(projects)) == len(runs)
            and all(not step.mutating and not step.paid for step in others)
        ):
            dropped_runs: list[tuple[int, str]] = []
            gone: set[int] = set()
            left: list[int] = []
            for at, step in enumerate(out):
                pid = _step_project(step)
                if step.action != "step.run_step" or pid is None:
                    continue
                reason = (await self._launch_blocked(probe, task_id, step, session_id)).get(pid)
                if reason:
                    dropped_runs.append((pid, reason))
                    gone.add(at)
                else:
                    left.append(pid)
            if dropped_runs and len(gone) < len(runs):
                out = [step for at, step in enumerate(out) if at not in gone]
                notes.append(
                    _blocked_launch_text(
                        ("Не запущен", "Не запущены"),
                        dropped_runs,
                        left,
                        ("запускается как обычно", "запускаются как обычно"),
                    )
                )
        if notes:
            self._launch_skipped[task_id] = " ".join(notes)
            logger.info("orch task {} launch: dropped blocked projects: {}", task_id, notes)
        return out

    def _with_defaults(self, step: PlanStep) -> PlanStep:
        fill = getattr(self.actions, "plan_defaults", None)
        if not callable(fill):
            return step
        try:
            params = fill(step.action, dict(step.params))
        except Exception:
            logger.exception("orch plan defaults failed for {}", step.action)
            return step
        if not isinstance(params, dict) or params == dict(step.params):
            return step
        return step.model_copy(update={"params": params})

    async def _resolved(self, step: PlanStep) -> PlanStep:
        fill = getattr(self.actions, "plan_resolve", None)
        if not callable(fill):
            return step
        try:
            params = await fill(step.action, dict(step.params))
        except PlanResolveRefused:
            raise
        except Exception:
            logger.exception("orch plan resolve failed for {}", step.action)
            return step
        if not isinstance(params, dict) or params == dict(step.params):
            return step
        return step.model_copy(update={"params": params})

    def _planner_last(self, task_id: int, name: str) -> Any:
        """``last_*`` планировщика этой задачи: снимок, а не общий объект."""
        meta = self._planner_meta.get(task_id)
        if meta is None:
            return getattr(self.planner, name, None)
        return meta.get(name)

    def _critic_last(self, task_id: int, name: str) -> Any:
        meta = self._critic_meta.get(task_id)
        if meta is None:
            return getattr(self.critic, name, None)
        return meta.get(name)

    async def _snapshot_agent(
        self,
        store: dict[int, dict[str, Any]],
        agent: Any,
        task_id: int,
        awaitable: Any,
    ) -> Any:
        """``last_*`` агента снимаются в той же задаче asyncio, что получила ответ, без переключения."""
        try:
            return await awaitable
        finally:
            store[task_id] = {name: getattr(agent, name, None) for name in _AGENT_LAST_FIELDS}

    def _planner_reason(self, task_id: int) -> str:
        from app.services.plain_ru import plain_ru
        from app.services.redact import redact_text

        raw = str(self._planner_last(task_id, "last_reason") or "")
        return " ".join(plain_ru(redact_text(raw)).split())[:300]

    def _asks_approval(self, task_id: int) -> bool:
        if budget_exhausted_response(self._planner_last(task_id, "last_response")):
            return False
        return bool(_ASKS_APPROVAL_RE.search(self._planner_reason(task_id)))

    async def _unknown_model_kind(self, task_id: int) -> str | None:
        """Настоящий вид ноды (text/image/video) из её состояния, когда планировщик пишет «модели нет».

        Ключ ноды берётся из причины планировщика, иначе — единственная нода, которую называет
        просьба. Не прочитали — ``None``: ответ не смешивает модели разных видов.
        """
        from app.services.redact import redact_text

        reason = redact_text(str(self._planner_last(task_id, "last_reason") or ""))
        goal = str((self.tasks.get(task_id) or {}).get("goal") or "")
        if not task_summary.unknown_model_name(reason, goal):
            return None
        project_id = self._task_project_id(task_id)
        if project_id is None:
            found = _REASON_PROJECT.search(f"{reason} {goal}")
            project_id = int(found.group(1)) if found else None
        if project_id is None:
            return None
        try:
            meta = await asyncio.wait_for(_load_node_meta(project_id), timeout=_NODE_META_TIMEOUT_S)
        except Exception:  # noqa: BLE001 — без состояния ноды вид угадывается по словам
            logger.exception("orch task {} node kind for unknown model not read", task_id)
            return None
        return _node_model_kind(meta, reason, goal)

    def _note_empty_reason(
        self, task_id: int, *, from_code: bool = False, model_kind: str | None = None
    ) -> None:
        if from_code:
            from app.services.plain_ru import plain_ru
            from app.services.redact import redact_text

            # «Возвращать нечего», «какую правку вернуть?»: текст собран кодом, целиком и без «не понял».
            why = " ".join(
                plain_ru(redact_text(str(self._planner_last(task_id, "last_reason") or ""))).split()
            )
            if why and not task_summary.raw_reason(why):
                self._fail_detail[task_id] = why.rstrip(".") + "."
                self._code_answers.add(task_id)
                return
        from app.services.redact import redact_text

        why = self._planner_reason(task_id)
        goal = str((self.tasks.get(task_id) or {}).get("goal") or "")
        # «Создай 5 проектов из файла»: файла в чате нет — спрашиваем темы, а не «не понял».
        ask = task_summary.file_clarify_text(goal)
        if ask:
            self._fail_detail[task_id] = ask
            self._code_answers.add(task_id)
            return
        # «Модели gpt-5 нет у ноды»: просьбу поняли — спрашиваем, какую из моделей ноды поставить.
        unknown = task_summary.unknown_model_text(
            " ".join(redact_text(str(self._planner_last(task_id, "last_reason") or "")).split()) or why,
            goal,
            kind=model_kind,
        )
        if unknown:
            self._fail_detail[task_id] = unknown
            self._code_answers.add(task_id)
            return
        refused = task_summary.delete_refusal_text(goal)
        if refused:
            self._fail_detail[task_id] = refused
            self._code_answers.add(task_id)
            return
        # «В каталоге нет действия для удаления проекта»: просьбу поняли, но чат так не умеет.
        missing = task_summary.missing_action_text(why, goal)
        if missing:
            self._fail_detail[task_id] = missing
            self._code_answers.add(task_id)
            return
        if why and _ASKS_APPROVAL_RE.search(why):
            self._fail_detail[task_id] = _ASKED_APPROVAL
            return
        # Планировщик понял просьбу и спрашивает уточнение: его вопрос человеческими словами
        # (без id, ключей нод и полей плана) вместо общего «Не понял, что сделать».
        ask = task_summary.clarify_text(str(self._planner_last(task_id, "last_reason") or ""))
        if ask:
            self._fail_detail[task_id] = ask
            self._code_answers.add(task_id)
        elif why and not task_summary.raw_reason(why):
            self._fail_detail[task_id] = why.rstrip(".") + "."

    async def _draft_plan(
        self,
        task_id: int,
        session_id: str,
        goal: str,
        context: str,
    ) -> list[PlanStep] | None:
        while True:
            try:
                drafted = await self._cancellable(
                    session_id,
                    self._snapshot_agent(
                        self._planner_meta,
                        self.planner,
                        task_id,
                        self.planner.make_plan(
                            goal,
                            context=context,
                            **self._llm_kwargs(task_id, AgentRole.planner.value),
                        ),
                    ),
                )
                # Флаг из снимка задачи: общий планировщик сосед уже мог перезаписать.
                self._from_code[task_id] = bool(self._planner_last(task_id, "last_from_code"))
                return drafted
            except PlanSchemaError as exc:
                why = str(exc)
                self._trace_plan(task_id, "schema_rejected", [], why)
                if await self._absorb_budget(
                    task_id, session_id, self._planner_last(task_id, "last_response")
                ):
                    return None
                used = self._schema_redo.get(task_id, 0)
                if used >= _SCHEMA_REDO_LIMIT:
                    self._fail_detail[task_id] = _SCHEMA_FAILED
                    await self._apply_block(task_id, session_id, "plan_schema")
                    return None
                self._schema_redo[task_id] = used + 1
                context = (context + "\n" + _SCHEMA_FEEDBACK.format(why=why)).strip()

    def _trace_plan(self, task_id: int, stage: str, plan: list[PlanStep], reason: str = "") -> None:
        from app.services.redact import redact_text

        response = self._planner_last(task_id, "last_response")
        raw = " ".join(redact_text(str(getattr(response, "text", "") or "")).split())
        why = " ".join(redact_text(str(reason or "")).split())
        actions = ",".join(step.action for step in plan[:10])
        logger.info(
            "orch task {} plan trace {}: steps={} actions=[{}] reason={} raw={}",
            task_id,
            stage,
            len(plan),
            actions,
            why[:_TRACE_REASON],
            raw[:_TRACE_RAW],
        )

    async def _review_plan(
        self,
        task_id: int,
        session_id: str,
        plan: list[PlanStep],
        context: str,
    ) -> str | None:
        if task_id in self._budget_stop:
            await self._settle_stopped(task_id, session_id)
            return None
        if _free_read_plan(plan, self.actions):
            return "approve"
        if _resume_after_read_plan(plan, self.actions) and _critic_checks_clear(plan):
            # «Продолжи проект 10»: [project.status, project.continue_pipeline]. Критик дважды
            # просил «прочитать состояние», хотя чтение уже стоит первым шагом, и гонял планировщик
            # по кругу >150 с. Продолжение само перепроверяет состояние условиями запуска в момент
            # исполнения, а риск high всё равно идёт карточкой человеку.
            return "approve"
        if _queue_launch_plan(plan, self.actions) and _critic_checks_clear(plan):
            # «Запусти проекты 5 6 7 параллельно»: очередь и число параллельных идут только через
            # карточку человека. Критик просил «сначала прочитай статус» и гонял планировщик по
            # кругу: карточка появлялась через 2+ минуты. Проверку делает человек кнопкой.
            return "approve"
        if self._code_plans.get(task_id) == _plan_fingerprint(plan):
            # План отката собран кодом из журнала, а не моделью: критику нечего проверять,
            # а подтверждение человека шаг с высоким риском всё равно получает карточкой.
            return "approve"
        # Подсказки проверки плана («Проверка плана …: исправь план, только id из каталога …») —
        # для планировщика, и к критику план приходит уже после неё. Задача 348: критик прочитал
        # подсказку про прошлый, отклонённый план и вернул её как revise к исправленному.
        context = _critic_context(context)
        skipped = self._launch_skipped.get(int(task_id))
        if skipped:
            # Проекты, убранные кодом до карточки, — не ошибка плана: критик не должен просить
            # вернуть их или эскалировать из-за «пропавшего» проекта из просьбы.
            context = (context + "\n" + _LAUNCH_SKIPPED_CRITIC.format(note=skipped)).strip()
        token = _LLM_TASK.set(int(task_id))
        try:
            verdict = str(
                await self._cancellable(
                    session_id,
                    self._snapshot_agent(
                        self._critic_meta,
                        self.critic,
                        task_id,
                        self.critic.review(
                            plan,
                            context=context,
                            **self._llm_kwargs(task_id, AgentRole.critic.value),
                        ),
                    ),
                )
            )
        finally:
            _LLM_TASK.reset(token)
        if await self._absorb_budget(task_id, session_id, self._critic_last(task_id, "last_response")):
            return None
        return verdict

    async def absorb_budget(
        self,
        task_id: int,
        session_id: str,
        response: Any,
        position: int | None = None,
    ) -> bool:
        """Публичная остановка по суб-бюджету. На терминальной задаче — no-op.

        ``InvalidTransition`` не выходит наружу: очередь не должна
        принимать её за сбой классификатора.
        """
        status = self._status(task_id)
        if status is None or status in TERMINAL_STATUSES:
            return False
        if status is TaskStatus.waiting_approval and task_id in self._budget_stop:
            return True
        try:
            return await self._absorb_budget(task_id, session_id, response, position=position)
        except InvalidTransition:
            logger.warning("absorb_budget skipped for task {} in {}", task_id, status.value)
            return False

    async def _absorb_budget(
        self,
        task_id: int,
        session_id: str,
        response: Any,
        position: int | None = None,
    ) -> bool:
        """Чекпоинт и waiting_approval, если роутер сообщил исчерпание суб-бюджета."""
        if not budget_exhausted_response(response):
            return False
        if (
            self._status(task_id) is TaskStatus.waiting_approval
            and self._block_reason.get(task_id) == ApprovalReason.missing_project_id.value
        ):
            self._budget_stop.add(task_id)
            return True
        if task_id not in self._budget_stop:
            self._budget_stop.add(task_id)
            text = str(getattr(response, "text", "") or "")
            self._context[task_id] = (self._context.get(task_id, "") + "\n" + text).strip()
            plan = self._stored_plan(task_id)
            existing = await asyncio.to_thread(self.checkpoints.load_latest, task_id)
            if existing is not None and not plan:
                plan = list(existing.plan)
            if (
                existing is not None
                and plan
                and _plan_fingerprint(list(existing.plan)) != _plan_fingerprint(plan)
            ):
                position = 0
            elif position is None:
                position = int(existing.position) if existing is not None else 0
            await self.bus.publish(
                session_id,
                "message",
                {"role": "assistant", "text": text, "kept": True},
                task_id=task_id,
            )
            await self._checkpoint(task_id, session_id, plan, position)
            approval = getattr(response, "approval", None)
            extra: dict[str, Any] | None = None
            if approval is not None and hasattr(approval, "model_dump"):
                extra = approval.model_dump()
                extra.pop("reason", None)
            await self._apply_block(
                task_id,
                session_id,
                ApprovalReason.budget_exhausted.value,
                extra=extra,
            )
        return True

    def _stored_plan(self, task_id: int) -> list[PlanStep]:
        row = self.tasks.get(task_id) or {}
        raw = row.get("plan_json")
        if not raw:
            return []
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return []
        if not isinstance(data, list):
            return []
        return [PlanStep.model_validate(item) for item in data]

    async def _screen_plan(
        self,
        task_id: int,
        session_id: str,
        plan: list[PlanStep],
        revise: bool = False,
    ) -> bool | str:
        """Локальные запреты, project_id и dry-run. True — план отклонён.

        Пустой план на этом пути — просьба не понята: ``cancelled`` с причиной
        ``empty_plan``, не сбой. Пустой чекпоинт на grant/resume остаётся ``failed``.
        """
        if not plan:
            await self._mention_waiting_card(task_id, session_id)
            if self._planner_last(task_id, "last_parse_failed") is True:
                # Пустой или обрезанный ответ модели — сбой, а не «не понял»: человек не виноват.
                await self._apply_block(task_id, session_id, "plan_unreadable")
                return True
            await self._close_not_understood(task_id, session_id)
            return True
        unknown = [step.action for step in plan if not_in_whitelist(step.action, self.actions)]
        if unknown:
            names = ", ".join(sorted(set(unknown)))
            if revise:
                return (
                    f"Проверка плана: действий {names} нет в каталоге. "
                    "Перепиши план, используя только id из каталога действий."
                )
            await self._apply_block(task_id, session_id, "not_in_whitelist")
            return True
        if revise and _proposes_prompt_text(plan):
            return _PROMPT_PROPOSE
        if ceiling_blocks(server_forecast_limit()) or forecast_unknown(plan):
            await self._checkpoint(task_id, session_id, plan, 0)
            await self._apply_block(task_id, session_id, "unknown_cost")
            return True
        for step in plan:
            if frame_soft_delete_state(step.action, self.actions) == "forbidden":
                await self._apply_block(task_id, session_id, "deletion_forbidden")
                return True
            if step_is_deletion(step, self.actions):
                await self._apply_block(task_id, session_id, "deletion_blocked")
                return True
        if plan_missing_verify(plan):
            await self._checkpoint(task_id, session_id, plan, 0)
            await self._apply_block(task_id, session_id, "plan_sanity")
            return True
        if self._task_project_id(task_id) is None and _plan_requires_project(plan, self.actions):
            named = _plan_single_project(plan, self.actions)
            if named is not None:
                await write_on_loop(self.tasks.set_project_id, task_id, named)
                logger.info("orch task {} bound to project {} named in plan", task_id, named)
        if self._task_project_id(task_id) is None and _plan_missing_project(plan, self.actions):
            saved = await asyncio.to_thread(self.checkpoints.load_latest, task_id)
            if saved is None or _plan_fingerprint(list(saved.plan)) != _plan_fingerprint(plan):
                await self._checkpoint(task_id, session_id, plan, 0)
            await self._apply_block(task_id, session_id, ApprovalReason.missing_project_id.value)
            return True
        refusal = await self._launch_refusal(task_id, plan)
        if refusal:
            self._fail_detail[task_id] = refusal
            await self._apply_block(task_id, session_id, "no_input_frames")
            return True
        self._plan_feedback.pop(task_id, None)
        dry = await self._dry_run(task_id, session_id, plan)
        if dry in {"budget", "missing_run", "project"}:
            return True
        if dry == "blocked":
            feedback = self._plan_feedback.pop(task_id, None)
            if revise and feedback is not None:
                return feedback[0]
            if feedback is not None:
                self._fail_detail[task_id] = feedback[1]
            await self._apply_block(task_id, session_id, "validator_blocked")
            return True
        return False

    async def _close_not_understood(self, task_id: int, session_id: str) -> None:
        self._block_reason[task_id] = "empty_plan"
        current = self._status(task_id)
        if current is None or current in TERMINAL_STATUSES or current is TaskStatus.waiting_approval:
            return
        await self._set_status(task_id, TaskStatus.cancelled, session_id=session_id, error="empty_plan")

    async def _launch_refusal(self, task_id: int, plan: list[PlanStep]) -> str | None:
        guard = self.launch_guard
        if guard is None:
            return None
        try:
            found = guard(plan, self._task_project_id(task_id))
            if inspect.isawaitable(found):
                found = await found
        except Exception:
            logger.exception("orch launch guard failed for task {}", task_id)
            return None
        return str(found) if found else None

    def _forget_risk_grants(self, task_id: int) -> None:
        self._risk_granted = {key for key in self._risk_granted if key[0] != task_id}
        self._grant_target.pop(task_id, None)
        self._step_grant = {key: value for key, value in self._step_grant.items() if key[0] != task_id}

    def _risk_grant_key(self, task_id: int, index: int, step: PlanStep) -> tuple[int, int, str]:
        return (task_id, index, paid_step_identity(step.action, step.params))

    async def _card_extra(self, task_id: int, step: PlanStep, goal: str = "") -> dict[str, Any]:
        prepare = getattr(self.actions, "prepare_card", None)
        if not callable(prepare):
            return {}
        # Подписи нод проекта: последствия шага называют ноду её именем, а не ключом.
        labels = await self._card_labels(task_id, [step])
        try:
            card = prepare(
                step.action,
                dict(step.params),
                {"task_id": task_id, "app": app_ref.current(), "goal": goal, "labels": labels},
            )
            if inspect.isawaitable(card):
                card = await card
        except Exception:
            logger.exception("orch card preview failed for {}", step.action)
            return {}
        return dict(card) if isinstance(card, dict) else {}

    def _executor_approval(self, task_id: int, index: int, step: PlanStep) -> dict[str, Any] | None:
        """Контекст одобрения для исполнителя и plan-sanity. Без гранта ключа нет."""
        if self._risk_grant_key(task_id, index, step) not in self._risk_granted:
            return None
        risk = effective_risk(self.actions, step)
        rule = escalation_for(step.action, step.params)
        if rule is not None:
            risk = max_risk(risk, coerce_risk(getattr(rule, "risk", "high")))
        view = approval_view(step.action, step.params, self.actions, fallback_risk=risk.value)
        cc = self._cc_change_id.get((task_id, step.action))
        decided = self._decided_change_id.get((task_id, step.action))
        own = self._step_grant.get((task_id, index))
        if own is not None:
            decided = own[0] or decided
            cc = own[1] if own[1] is not None else cc
        return {
            "change_id": decided,
            "action": step.action,
            "risk": view["risk"],
            "destructive": view["destructive"] is True,
            "granted": True,
            "cc_change_id": cc if isinstance(cc, str) else None,
        }

    def _save_plan(self, task_id: int, plan: list[PlanStep], position: int | None = None) -> None:
        """Новый план сбрасывает гранты риска и отметку approve этой задачи."""
        self._forget_risk_grants(task_id)
        self._approved.pop(task_id, None)
        if position is None:
            self.tasks.save_plan(task_id, plan)
            return
        self.tasks.save_plan(task_id, plan, position=position)

    async def _stop_if_budget(
        self,
        task_id: int,
        session_id: str,
        plan: list[PlanStep],
        index: int,
    ) -> bool:
        """Останов по суб-бюджету. Уже ждущая или терминальная задача не переводится повторно."""
        if task_id not in self._budget_stop:
            return False
        status = self._status(task_id)
        if status is None or status in TERMINAL_STATUSES or status is TaskStatus.waiting_approval:
            return True
        await self._checkpoint(task_id, session_id, plan, index)
        status = self._status(task_id)
        if status is None or status in TERMINAL_STATUSES or status is TaskStatus.waiting_approval:
            return True
        try:
            await self._apply_block(task_id, session_id, ApprovalReason.budget_exhausted.value)
        except InvalidTransition:
            status = self._status(task_id)
            if status is TaskStatus.waiting_approval or status in TERMINAL_STATUSES:
                return True
            raise
        return True

    async def _execute_plan(
        self,
        task_id: int,
        session_id: str,
        plan: list[PlanStep],
        start: int,
        goal: str,
    ) -> None:
        self._remember_paid(task_id)
        if any(not_in_whitelist(step.action, self.actions) for step in plan[start:]):
            await self._apply_block(task_id, session_id, "not_in_whitelist")
            return
        plan = [tighten_paid_step(self.actions, step) for step in plan]
        if forecast_unknown(plan):
            await self._apply_block(task_id, session_id, "unknown_cost")
            return
        index = start
        while index < len(plan):
            if await self._stop_if_budget(task_id, session_id, plan, index):
                return
            if await self._hold_for_approval(task_id, session_id, plan, index):
                return
            if session_id in self._pause and session_id not in self._kill:
                await self._checkpoint(task_id, session_id, plan, index)
                await self._set_status(task_id, TaskStatus.paused, session_id=session_id)
                return
            if session_id in self._cancel or session_id in self._kill:
                await self._set_status(task_id, TaskStatus.cancelled, session_id=session_id)
                return
            if session_id in self._steer:
                steered = await self._apply_steer(task_id, session_id, plan, index, goal)
                if steered is None:
                    await self._settle_stopped(task_id, session_id)
                    return
                plan, index = steered
                continue

            parked = self._grouped_unknown(plan, index, task_id)
            if parked is not None:
                unknown_at, unknown = parked
                self._remember_unknown_step(task_id, unknown_at, unknown)
                self._block_reason[task_id] = "paid_outcome_unknown"
                await self._checkpoint(task_id, session_id, plan, unknown_at)
                await self._escalate(
                    task_id,
                    session_id,
                    "paid_outcome_unknown",
                    extra={
                        "action": unknown.action,
                        "position": unknown_at,
                        "forecast_usd": forecast_cost([unknown]),
                        "decisions": approval_decision_choices(),
                    },
                )
                return

            batch = _readonly_batch(plan, index, self.actions)
            if len(batch) == 1:
                batch = self._paid_batch(task_id, plan, index)
            step = batch[0]
            # Платные запуски разных проектов одной группы идут вместе, каждый со своим гейтом.
            concurrent = len(batch) > 1 and not step_is_readonly(step, self.actions)
            frame = frame_soft_delete_state(step.action, self.actions) if len(batch) == 1 else None
            if frame == "forbidden":
                await self._apply_block(task_id, session_id, "deletion_forbidden")
                return
            if frame == "grant" and self._risk_grant_key(task_id, index, step) not in self._risk_granted:
                await self._checkpoint(task_id, session_id, plan, index)
                self._block_reason[task_id] = "destructive"
                card = await self._card_extra(task_id, step, goal)
                await self._escalate(
                    task_id,
                    session_id,
                    "destructive",
                    extra={**card, "action": step.action, "soft_delete": True, "gate": "soft_delete"},
                )
                return
            if any(not_in_whitelist(item.action, self.actions) for item in batch):
                await self._apply_block(task_id, session_id, "not_in_whitelist")
                return
            # Группа собирается только из read-only. Чужой шаг в батче — сбой,
            # не assert: под python -O assert исчез бы и шаг ушёл бы в исполнение.
            if (
                len(batch) > 1
                and any(not step_is_readonly(item, self.actions) for item in batch)
                and not _parallel_runs_ok(batch, self.actions)
            ):
                await self._fail_closed(task_id, session_id, "parallel_risk")
                return
            if len(batch) == 1 and step.paid and self._running_paid_row(task_id, step) is not None:
                await self._hold_paid_unknown(task_id, session_id, step)
                return

            if await self._gate_group(task_id, session_id, plan, index, batch, goal, concurrent=concurrent):
                return
            gated = list(enumerate(batch)) if concurrent else [(0, step)]
            for offset, item in gated:
                if await self._gate_step(task_id, session_id, plan, index + offset, item, goal, anchor=index):
                    return

            partial_row = self._open_partial_row(task_id, step, plan, index) if len(batch) == 1 else None
            if partial_row is not None:
                await self._checkpoint(task_id, session_id, plan, index)
                self._block_reason[task_id] = "partial_write"
                await self._escalate(
                    task_id,
                    session_id,
                    "partial_write",
                    extra={
                        "action": step.action,
                        "change_id": partial_row.get("change_id") if partial_row else None,
                    },
                )
                return

            skip = self._skip_reason(task_id, step, plan, index) if len(batch) == 1 else None
            if skip:
                await self.bus.publish(
                    session_id,
                    "paid_retry_blocked" if skip == "paid_retry" else "step_skipped",
                    {"position": index, "action": step.action, "reason": skip},
                    task_id=task_id,
                )
                index += 1
                continue

            unknown = next(
                (item for item in batch if item.paid and self._paid_outcome_unknown(task_id, item)),
                None,
            )
            if unknown is not None:
                # Строка running уже есть: шаг стартовал до рестарта, исход не записан.
                # Позиция — сам unknown, не начало батча: иначе approve берёт
                # предыдущий шаг (например verify_code_state перед run_node).
                unknown_at = index + batch.index(unknown)
                self._remember_unknown_step(task_id, unknown_at, unknown)
                self._block_reason[task_id] = "paid_outcome_unknown"
                await self._checkpoint(task_id, session_id, plan, unknown_at)
                await self._escalate(
                    task_id,
                    session_id,
                    "paid_outcome_unknown",
                    extra={
                        "action": unknown.action,
                        "position": unknown_at,
                        "forecast_usd": forecast_cost([unknown]),
                        "decisions": approval_decision_choices(),
                    },
                )
                return

            if any(item.paid for item in batch):
                if await self._stop_if_budget(task_id, session_id, plan, index):
                    return
                await self._checkpoint(task_id, session_id, plan, index)
            for offset, item in enumerate(batch):
                if step_is_readonly(item, self.actions):
                    continue
                at = index + offset if concurrent else index
                outcome, verdict = await self._validate(task_id, session_id, item, "before", at)
                if outcome == "budget":
                    await self._halt_for_validator_budget(task_id, session_id, plan, index, verdict)
                    return
                if outcome == "missing_run":
                    return
                if outcome == "project":
                    return
                if outcome == "blocked":
                    await self._fail_validation(task_id, session_id)
                    return

            ctx = {
                "task_id": task_id,
                "session_id": session_id,
                "run_id": orch_run_id(task_id=task_id),
                "budget_scope": BudgetScope.pipeline.value,
                "app": app_ref.current(),
                "bus": self.bus,
                "operator": f"orchestrator:task-{task_id}",
                "goal": goal,
            }
            if len(batch) == 1:
                granted = self._executor_approval(task_id, index, step)
                if granted is not None:
                    ctx["approval"] = granted
            started: list[tuple[PlanStep, int]] = []
            db_pos = self._next_step_position(task_id)
            for offset, item in enumerate(batch):
                await self.bus.publish(
                    session_id,
                    "step_started",
                    {"position": index + offset, "action": item.action, "paid": item.paid},
                    task_id=task_id,
                )
                row_id = await write_on_loop(self.tasks.insert_step, task_id, db_pos + offset, item)
                started.append((item, row_id))
            try:
                if len(batch) == 1:
                    done_step, step_id = started[0]
                    results = [
                        await self._run_one(
                            session_id,
                            done_step,
                            ctx,
                            task_id=task_id,
                            step_id=step_id,
                            plan=plan,
                            index=index,
                        )
                    ]
                elif concurrent:
                    results = await self._run_concurrent(
                        session_id, started, ctx, task_id=task_id, plan=plan, index=index
                    )
                else:
                    results = await self._run_parallel(session_id, batch, ctx)
            except asyncio.CancelledError:
                if any(item.paid for item, _ in started):
                    self._shield_orphaned.add(task_id)
                    raise
                if any(item.mutating for item, _ in started):
                    await asyncio.shield(
                        self._mark_mutating_partial(task_id, session_id, plan, index, started)
                    )
                    raise
                for _, step_id in started:
                    await write_on_loop(self.tasks.finish_step, step_id, "cancelled", None, 0.0)
                current = self._status(task_id)
                if current not in TERMINAL_STATUSES:
                    await self._set_status(task_id, TaskStatus.cancelled, session_id=session_id)
                return
            except Exception:
                logger.exception("orch step {} failed", step.action)
                for _, step_id in started:
                    await write_on_loop(self.tasks.finish_step, step_id, "failed", None, 0.0)
                await self._set_status(task_id, TaskStatus.failed, session_id=session_id, error="step_failed")
                await self.bus.publish(
                    session_id,
                    "task_failed",
                    {"error": "step_failed", "action": step.action},
                    task_id=task_id,
                )
                return

            if any(isinstance(result, BaseException) for result in results):
                for offset, ((done_step, step_id), result) in enumerate(zip(started, results, strict=True)):
                    if isinstance(result, BaseException):
                        state = "cancelled" if isinstance(result, asyncio.CancelledError) else "failed"
                        if self._claim_step(step_id):
                            await write_on_loop(self.tasks.finish_step, step_id, state, None, 0.0)
                        logger.warning("orch step {} failed in group: {}", done_step.action, result)
                    else:
                        at = index + offset if concurrent else index
                        await self._record(task_id, session_id, done_step, step_id, result, at)
                # Сосед мог уже перевести задачу в failed своей записью: второй раз не пишем.
                current = self._status(task_id)
                if current not in TERMINAL_STATUSES and current is not TaskStatus.waiting_approval:
                    await self._set_status(
                        task_id, TaskStatus.failed, session_id=session_id, error="step_failed"
                    )
                    await self.bus.publish(
                        session_id,
                        "task_failed",
                        {"error": "step_failed", "action": step.action},
                        task_id=task_id,
                    )
                return

            held = next(
                (
                    (item, offset, result)
                    for offset, (item, result) in enumerate(zip(started, results, strict=True))
                    if envelope_status(result) == "approval_required"
                ),
                None,
            )
            if held is not None:
                (done_step, step_id), offset, result = held
                if concurrent:
                    # Соседи по группе уже отработали: их результат и деньги пишутся до карточки.
                    for at, ((other, other_id), other_result) in enumerate(
                        zip(started, results, strict=True)
                    ):
                        if at != offset:
                            await self._record(task_id, session_id, other, other_id, other_result, index + at)
                await self._hold_executor_approval(
                    task_id,
                    session_id,
                    done_step,
                    step_id,
                    plan,
                    index + offset,
                    result,
                )
                return

            if concurrent and task_id in self._paid_outcome_hold:
                await self._settle_concurrent_stop(task_id, session_id, started, results, plan, index)
                return
            if task_id in self._paid_outcome_hold and any(item.paid for item, _ in started):
                await self._capture_unknown_paid(task_id, session_id, started, results, plan, index)
                if self._status(task_id) is TaskStatus.executing:
                    await self._mark_paid_outcome_unknown(session_id, task_id)
                return

            paid_in_batch = False
            rejected = False
            for offset, (item, result) in enumerate(zip(started, results, strict=True)):
                done_step, step_id = item
                if done_step.paid:
                    paid_in_batch = True
                if not await self._record(task_id, session_id, done_step, step_id, result, index + offset):
                    rejected = True
            if rejected:
                current = self._status(task_id)
                if current not in TERMINAL_STATUSES and current is not TaskStatus.waiting_approval:
                    await self._set_status(
                        task_id,
                        TaskStatus.failed,
                        session_id=session_id,
                        error="action_rejected",
                    )
                    await self.bus.publish(
                        session_id,
                        "task_failed",
                        {"error": "action_rejected", "action": step.action},
                        task_id=task_id,
                    )
                return

            next_index = index + len(batch)
            # last_response у ActionBackedExecutor остаётся None: LLM перед
            # действием нет. Кастомный исполнитель может выставить ответ роутера
            # (задел, пока WS2 не отдаёт бюджет из actions.execute).
            if await self._absorb_budget(
                task_id,
                session_id,
                getattr(self.executor, "last_response", None),
                position=next_index,
            ):
                return

            for offset, (item, _result) in enumerate(zip(started, results, strict=True)):
                done_step, _step_id = item
                if not step_is_readonly(done_step, self.actions):
                    outcome, verdict = await self._validate(
                        task_id, session_id, done_step, "after", index + offset
                    )
                    if outcome == "budget":
                        await self._halt_for_validator_budget(
                            task_id,
                            session_id,
                            plan,
                            index + offset + 1,
                            verdict,
                        )
                        return
                    if outcome == "missing_run":
                        return
                    if outcome == "project":
                        return
                    if outcome == "blocked":
                        await self._fail_validation(task_id, session_id)
                        return

            plan = await self._insert_follow_up(task_id, plan, index + len(batch), started, results, goal)
            index += len(batch)
            await self._checkpoint(task_id, session_id, plan, index)

            if session_id in self._cancel or session_id in self._kill:
                if paid_in_batch:
                    await self._checkpoint(task_id, session_id, plan, index)
                    await self._set_status(task_id, TaskStatus.paused, session_id=session_id)
                else:
                    await self._set_status(task_id, TaskStatus.cancelled, session_id=session_id)
                return
            if session_id in self._pause:
                await self._checkpoint(task_id, session_id, plan, index)
                await self._set_status(task_id, TaskStatus.paused, session_id=session_id)
                return
            if await self._hold_for_approval(task_id, session_id, plan, index):
                return

        if await self._stop_if_budget(task_id, session_id, plan, index):
            return
        await self._set_status(task_id, TaskStatus.done, session_id=session_id)
        await self.bus.publish(session_id, "task_done", {"task_id": task_id}, task_id=task_id)

    async def _insert_follow_up(
        self,
        task_id: int,
        plan: list[PlanStep],
        at: int,
        started: list[tuple[PlanStep, int]],
        results: list[Any],
        goal: str,
    ) -> list[PlanStep]:
        """Правку промта подготовили — следующим шагом той же задачи её применение.

        Шаг ``change.apply`` берётся из результата ``prompt.edit_text`` (``apply_step``) и идёт
        своим гейтом: риск high, отдельная карточка и отдельное одобрение человека, как если
        бы он написал «примени правку промта». Сам по себе он не выполняется никогда.
        """
        follow = _follow_up_steps(plan, started, results, goal)
        if not follow:
            return plan
        shift = len(follow)
        moved = {
            (key[0], key[1] + shift, key[2])
            for key in self._risk_granted
            if key[0] == task_id and key[1] >= at
        }
        self._risk_granted = {
            key for key in self._risk_granted if not (key[0] == task_id and key[1] >= at)
        } | moved
        self._step_grant = {
            ((key[0], key[1] + shift) if key[0] == task_id and key[1] >= at else key): value
            for key, value in self._step_grant.items()
        }
        out = [*plan[:at], *follow, *plan[at:]]
        await write_on_loop(self.tasks.save_plan, task_id, out)
        # Одобренный план — тот же, с добавленным шагом: его гейт всё равно спросит человека.
        self._approved[task_id] = _plan_fingerprint(out)
        logger.info("orch task {} follow-up {} after prompt edit", task_id, [s.action for s in follow])
        return out

    async def _gate_step(
        self,
        task_id: int,
        session_id: str,
        plan: list[PlanStep],
        index: int,
        step: PlanStep,
        goal: str,
        *,
        anchor: int,
    ) -> bool:
        """Гейт риска и paid/destructive одного шага. True — карточка открыта, исполнение стоит.

        Шаг параллельной группы (``index > anchor``) спрашивается своей карточкой,
        но чекпоинт стоит на начале группы: после grant группа собирается снова
        и уже одобренные соседи не теряются. Грант ложится на ключ самого шага.
        """
        risk, gate = self._step_gate(step)
        granted = self._risk_grant_key(task_id, index, step) in self._risk_granted
        if granted or (risk not in APPROVAL_RISKS and not gate):
            return False
        await self._checkpoint(task_id, session_id, plan, anchor)
        self._grant_target.pop(task_id, None)
        if risk in APPROVAL_RISKS:
            card = await self._card_extra(task_id, step, goal)
            await self._escalate(
                task_id,
                session_id,
                "risk",
                extra={**card, "action": step.action, "risk": risk.value, "plan_risk": step.risk},
            )
            self._aim_grant(task_id, index, anchor)
            return True
        reason = "paid" if gate == "paid" else "destructive"
        extra = {
            **await self._card_extra(task_id, step, goal),
            "action": step.action,
            "risk": risk.value,
            "plan_risk": step.risk,
            "gate": gate,
        }
        if gate == "unknown":
            extra["paid"] = True
            extra["destructive"] = True
        self._block_reason[task_id] = reason
        await self._escalate(task_id, session_id, reason, extra=extra)
        self._aim_grant(task_id, index, anchor)
        return True

    def _step_gate(self, step: PlanStep) -> tuple[Risk, str | None]:
        """Риск шага (реестр, план, правило эскалации) и ворота ядра: paid / destructive / unknown."""
        risk = effective_risk(self.actions, step)
        rule = escalation_for(step.action, step.params)
        if rule is not None:
            risk = max_risk(risk, coerce_risk(getattr(rule, "risk", "high")))
        gate = metadata_call_gate(step.action, self.actions)
        if rule is not None and bool(getattr(rule, "destructive", False)) and gate is None:
            gate = "destructive"
        elif rule is not None and bool(getattr(rule, "paid", False)) and gate is None:
            gate = "paid"
        return risk, gate

    def _same_action_run(self, plan: list[PlanStep], index: int) -> list[int]:
        """Подряд идущие шаги того же действия и той же parallel_group, каждый в своём проекте."""
        step = plan[index]
        if _step_project(step) is None or _long_core_step(step.action):
            return [index]
        if frame_soft_delete_state(step.action, self.actions) is not None:
            return [index]
        seen = {_step_project(step)}
        found = [index]
        cursor = index + 1
        while cursor < len(plan):
            nxt = plan[cursor]
            project = _step_project(nxt)
            if nxt.action != step.action or nxt.parallel_group != step.parallel_group:
                break
            if project is None or project in seen:
                break
            seen.add(project)
            found.append(cursor)
            cursor += 1
        return found

    def _approval_group(
        self,
        task_id: int,
        plan: list[PlanStep],
        index: int,
        batch: list[PlanStep],
        concurrent: bool,
    ) -> list[int]:
        """Позиции шагов, которые спросит одна карточка. Меньше двух — карточки по одной.

        Группа — шаги одного действия в разных проектах с одной причиной карточки:
        запуски одной parallel_group или подряд идущие шаги без долгого платного
        хода. Уже одобренный или не требующий карточки шаг в неё не входит.
        """
        candidates = (
            list(range(index, index + len(batch))) if concurrent else self._same_action_run(plan, index)
        )
        found: list[int] = []
        kind: tuple[str, str] | None = None
        for at in candidates:
            step = plan[at]
            risk, gate = self._step_gate(step)
            needs = self._risk_grant_key(task_id, at, step) not in self._risk_granted and (
                risk in APPROVAL_RISKS or bool(gate)
            )
            if not needs:
                if concurrent:
                    continue
                break
            if gate == "unknown":
                return []
            mark = (step.action, _card_reason(risk, gate))
            if kind is None:
                kind = mark
            elif mark != kind:
                if concurrent:
                    return []
                break
            found.append(at)
        if not concurrent and found[:1] != [index]:
            return []
        return found if len(found) > 1 else []

    async def _gate_group(
        self,
        task_id: int,
        session_id: str,
        plan: list[PlanStep],
        index: int,
        batch: list[PlanStep],
        goal: str,
        *,
        concurrent: bool,
    ) -> bool:
        """Одна карточка на однотипные шаги разных проектов. True — карточка открыта.

        Grant этой карточки одобряет ровно шаги из её списка, и только пока их
        отпечаток совпадает с планом. Валидаторы, бюджет и запись трат идут
        у каждого шага отдельно, как без группы.
        """
        positions = self._approval_group(task_id, plan, index, batch, concurrent)
        if not positions:
            return False
        steps = [plan[at] for at in positions]
        gates = [self._step_gate(step) for step in steps]
        risk = max_risk(*(item[0] for item in gates))
        gate = gates[0][1]
        reason = _card_reason(gates[0][0], gate)
        await self._checkpoint(task_id, session_id, plan, index)
        self._grant_target.pop(task_id, None)
        labels = await self._card_labels(task_id)
        views = [approval_steps.step_view(step, self.actions, labels) for step in steps]
        self._settings_cards(task_id, steps, views)
        previews = [await self._card_extra(task_id, step, goal) for step in steps]
        mark = group_fingerprint(plan, positions)
        card = approval_steps.group_card(steps, positions, views, previews, mark, concurrent=concurrent)
        self._settings_card_note(task_id, card)
        card["risk"] = max_risk(risk, card["risk"]).value
        extra = {**card, "action": steps[0].action, "plan_risk": steps[0].risk}
        if reason != "risk":
            extra["gate"] = gate
            self._block_reason[task_id] = reason
        await self._escalate(task_id, session_id, reason, extra=extra)
        opened = self._appr_open.get(_appr_key(task_id))
        if opened:
            self._group_cards[opened] = (tuple(positions), mark)
        return True

    def card_group(self, task_id: int, card: str | None, plan: list[PlanStep]) -> list[int] | None:
        """Позиции групповой карточки ``card``. None — карточка не групповая.

        Пустой список — план уже не тот, что показан человеку: одобрять нечего,
        шаги спросят новой карточкой.
        """
        found = self._group_cards.get(card or "")
        if found is None or self._appr_owner.get(card or "", (None,))[0] != int(task_id):
            return None
        positions, mark = found
        if any(not 0 <= at < len(plan) for at in positions):
            return []
        if group_fingerprint(plan, list(positions)) != mark:
            return []
        return list(positions)

    def _grant_group(self, task_id: int, card: str, plan: list[PlanStep], positions: list[int]) -> None:
        """Грант каждому шагу группы со своим id одобрения: мост привязал к нему план ядра шага."""
        for at in positions:
            self._risk_granted.add(self._risk_grant_key(task_id, at, plan[at]))
            self._step_grant[(task_id, at)] = (group_step_change_id(card, at), None)

    def _aim_grant(self, task_id: int, index: int, anchor: int) -> None:
        """Карточка шага группы: grant этой карточки ляжет на ``index``, не на чекпоинт."""
        if index == anchor:
            return
        card = self._appr_open.get(_appr_key(task_id))
        if card:
            self._grant_target[task_id] = (index, card)

    def _granted_position(self, task_id: int, plan: list[PlanStep], position: int, card: str | None) -> int:
        """Позиция гранта: шаг группы, чью карточку одобрили, иначе позиция чекпоинта."""
        found = self.card_position(task_id, card, plan, position)
        self._grant_target.pop(task_id, None)
        return found

    def card_position(self, task_id: int, card: str | None, plan: list[PlanStep], position: int) -> int:
        """Шаг плана, о котором карточка ``card``. Мост подтверждает план именно этого шага."""
        aimed = self._grant_target.get(task_id)
        if aimed is None or not card or aimed[1] != card:
            return position
        target = aimed[0]
        if not position < target < len(plan):
            return position
        group = plan[position].parallel_group
        if not group or any(item.parallel_group != group for item in plan[position : target + 1]):
            return position
        return target

    def _snapshot_grant(self, task_id: int, index: int, step: PlanStep) -> None:
        """Решение карточки за позицией: соседи группы с тем же действием его не затирают."""
        decided = self._decided_change_id.get((task_id, step.action))
        cc = self._cc_change_id.get((task_id, step.action))
        if decided or isinstance(cc, str):
            self._step_grant[(task_id, index)] = (decided, cc if isinstance(cc, str) else None)

    def _paid_batch(self, task_id: int, plan: list[PlanStep], index: int) -> list[PlanStep]:
        """Подряд идущие запуски разных проектов одной parallel_group.

        Группа берётся целиком или не берётся: шаг с незакрытым исходом,
        partial или повтором уже оплаченного идёт обычным путём по одному.
        """
        step = plan[index]
        group = step.parallel_group
        if not group:
            return [step]
        batch = [step]
        cursor = index + 1
        while cursor < len(plan) and plan[cursor].parallel_group == group:
            batch.append(plan[cursor])
            cursor += 1
        if not _parallel_runs_ok(batch, self.actions):
            return [step]
        for offset, item in enumerate(batch):
            at = index + offset
            if self._running_paid_row(task_id, item) is not None:
                return [step]
            if self._open_partial_row(task_id, item, plan, at) is not None:
                return [step]
            if self._skip_reason(task_id, item, plan, at):
                return [step]
        return batch

    async def _run_concurrent(
        self,
        session_id: str,
        started: list[tuple[PlanStep, int]],
        ctx: dict[str, Any],
        *,
        task_id: int,
        plan: list[PlanStep],
        index: int,
    ) -> list[Any]:
        """Каждый шаг — свой ``_run_one`` со своим одобрением. Сбой одного не гасит соседей."""
        resume_at = index + len(started)
        calls = []
        for offset, (item, step_id) in enumerate(started):
            own = dict(ctx)
            own.pop("approval", None)
            granted = self._executor_approval(task_id, index + offset, item)
            if granted is not None:
                own["approval"] = granted
            calls.append(
                self._run_one(
                    session_id,
                    item,
                    own,
                    task_id=task_id,
                    step_id=step_id,
                    plan=plan,
                    index=index + offset,
                    resume_at=resume_at,
                )
            )
        return list(await asyncio.gather(*calls, return_exceptions=True))

    async def _settle_concurrent_stop(
        self,
        task_id: int,
        session_id: str,
        started: list[tuple[PlanStep, int]],
        results: list[Any],
        plan: list[PlanStep],
        index: int,
    ) -> None:
        """Стоп во время группы запусков. Все исходы уже известны: пишем каждый, задача cancelled."""
        while task_id in self._paid_unknown_marking:
            await asyncio.sleep(0)
        self._paid_outcome_hold.discard(task_id)
        for offset, ((item, step_id), result) in enumerate(zip(started, results, strict=True)):
            if isinstance(result, BaseException):
                if self._claim_step(step_id):
                    state = "cancelled" if isinstance(result, asyncio.CancelledError) else "failed"
                    await write_on_loop(self.tasks.finish_step, step_id, state, None, 0.0)
                continue
            await self._record(task_id, session_id, item, step_id, result, index + offset)
        if self._block_reason.get(task_id) in _PAID_OUTCOME_REASONS:
            self._block_reason.pop(task_id, None)
        await self._checkpoint(task_id, session_id, plan, index + len(started))
        current = self._status(task_id)
        if current is not None and current not in TERMINAL_STATUSES:
            await self._set_status(task_id, TaskStatus.cancelled, session_id=session_id)

    async def _apply_steer(
        self,
        task_id: int,
        session_id: str,
        plan: list[PlanStep],
        index: int,
        goal: str,
    ) -> tuple[list[PlanStep], int] | None:
        text = self._steer.pop(session_id)
        ctx = (self._context.get(task_id, "") + "\n" + text).strip()
        self._context[task_id] = ctx
        await self._checkpoint(task_id, session_id, plan, index)
        revised = await self._make_plan(task_id, session_id, goal, ctx)
        if revised is None:
            await self._settle_stopped(task_id, session_id)
            return None
        await write_on_loop(self._save_plan, task_id, revised, position=0)
        if await self._screen_plan(task_id, session_id, revised):
            return None
        await self._set_status(task_id, TaskStatus.critic_review, session_id=session_id)
        verdict = await self._review_plan(task_id, session_id, revised, ctx)
        if verdict is None:
            await self._settle_stopped(task_id, session_id)
            return None
        if verdict != "approve":
            await self._checkpoint(task_id, session_id, revised, 0)
            note = (
                _CRITIC_NO_REASON if verdict == "escalate" else self._critic_note(task_id, _CRITIC_REJECTED)
            )
            await self._apply_block(
                task_id, session_id, "steer_not_approved", extra={"warnings": [note]} if note else None
            )
            return None
        self._approved[task_id] = _plan_fingerprint(revised)
        await self._set_status(task_id, TaskStatus.executing, session_id=session_id)
        return revised, 0

    async def _cancellable(self, session_id: str, awaitable: Any) -> Any:
        inner: asyncio.Task[Any] = asyncio.create_task(awaitable)
        self._unpaid[session_id] = inner
        try:
            return await inner
        finally:
            if self._unpaid.get(session_id) is inner:
                self._unpaid.pop(session_id, None)

    async def _run_one(
        self,
        session_id: str,
        step: PlanStep,
        ctx: dict[str, Any],
        *,
        task_id: int,
        step_id: int,
        plan: list[PlanStep],
        index: int,
        resume_at: int | None = None,
    ) -> Any:
        async def _call() -> Any:
            return await self.executor.run(step, ctx)

        if step.paid or step.mutating:
            inner: asyncio.Task[Any] = asyncio.create_task(_call())
            self._remember_inflight_write(task_id, inner)
            if step.paid:
                self._paid_open.add(task_id)
                self._paid_open_count[task_id] = self._paid_open_count.get(task_id, 0) + 1
                peers = self._paid_inner.setdefault(task_id, set())
                peers.add(inner)
                after = index + 1 if resume_at is None else resume_at
                inner.add_done_callback(
                    lambda done, _task=task_id, _session=session_id, _step=step, _sid=step_id, _plan=plan, _index=index, _after=after: (
                        self._on_shield_done(
                            done,
                            task_id=_task,
                            session_id=_session,
                            step=_step,
                            step_id=_sid,
                            plan=_plan,
                            index=_index,
                            resume_at=_after,
                        )
                    )
                )
                inner.add_done_callback(lambda done, _task=task_id: self._drop_paid_inner(_task, done))
            else:
                self._mutating_open[task_id] = step_id
                inner.add_done_callback(
                    lambda done, _task=task_id, _session=session_id, _sid=step_id, _index=index: (
                        self._on_mutating_done(
                            done,
                            task_id=_task,
                            session_id=_session,
                            step_id=_sid,
                            index=_index,
                        )
                    )
                )
            try:
                return await asyncio.shield(inner)
            finally:
                if step.paid:
                    left = self._paid_open_count.get(task_id, 1) - 1
                    if left > 0:
                        self._paid_open_count[task_id] = left
                    else:
                        self._paid_open_count.pop(task_id, None)
                        self._paid_open.discard(task_id)
                else:
                    self._mutating_open.pop(task_id, None)
        return await self._cancellable(session_id, _call())

    def _remember_inflight_write(self, task_id: int, inner: asyncio.Task[Any]) -> None:
        """Держать inner, пока approve partial не дождётся конца записи."""
        key = int(task_id)
        self._inflight_write[key] = inner

        def _drop(done: asyncio.Task[Any], stored: int = key, job: asyncio.Task[Any] = inner) -> None:
            if self._inflight_write.get(stored) is job and done.done():
                self._inflight_write.pop(stored, None)

        inner.add_done_callback(_drop)

    def _drop_paid_inner(self, task_id: int, done: asyncio.Task[Any]) -> None:
        peers = self._paid_inner.get(task_id)
        if peers is None:
            return
        peers.discard(done)
        if not peers:
            self._paid_inner.pop(task_id, None)

    def _on_shield_done(
        self,
        done: asyncio.Task[Any],
        *,
        task_id: int,
        session_id: str,
        step: PlanStep,
        step_id: int,
        plan: list[PlanStep],
        index: int,
        resume_at: int | None = None,
    ) -> None:
        """Сторож: платный шаг дожил после отмены внешней задачи — записать результат.

        В группе запусков пауза или сбой задачи — только когда кончился последний сосед.
        """
        if done.cancelled() or task_id not in self._shield_orphaned or task_id in self._paid_outcome_hold:
            return
        try:
            exc = done.exception()
        except asyncio.CancelledError:
            return
        live = [peer for peer in self._paid_inner.get(task_id, set()) if peer is not done and not peer.done()]
        if exc is not None:
            if self._claim_step(step_id):
                self.tasks.finish_step(step_id, "failed", None, 0.0)
            if live:
                self._orphan_failed.add(task_id)
                return
            self._orphan_failed.discard(task_id)
            self._shield_orphaned.discard(task_id)
            loop = asyncio.get_running_loop()
            job = loop.create_task(
                self._fail_closed(task_id, session_id, "paid_step_failed"),
                name=f"orch-shield-fail-{task_id}",
            )
            self._shield_jobs.add(job)
            job.add_done_callback(self._shield_jobs.discard)
            return
        if self._claim_step(step_id):
            self._write_step_done(task_id, step, step_id, done.result(), index)
        elif not live and task_id not in self._orphan_failed:
            return
        if live:
            return
        loop = asyncio.get_running_loop()
        if task_id in self._orphan_failed:
            self._orphan_failed.discard(task_id)
            self._shield_orphaned.discard(task_id)
            job = loop.create_task(
                self._fail_closed(task_id, session_id, "paid_step_failed"),
                name=f"orch-shield-fail-{task_id}",
            )
            self._shield_jobs.add(job)
            job.add_done_callback(self._shield_jobs.discard)
            return
        job = loop.create_task(
            self._pause_after_shield(
                task_id, session_id, plan, index + 1 if resume_at is None else resume_at
            ),
            name=f"orch-shield-{task_id}",
        )
        self._shield_jobs.add(job)
        job.add_done_callback(self._shield_jobs.discard)

    def _on_mutating_done(
        self,
        done: asyncio.Task[Any],
        *,
        task_id: int,
        session_id: str,
        step_id: int,
        index: int,
    ) -> None:
        """Поздний ``change_id`` unpaid mutating после kill. Шаг остаётся ``partial``.

        Событие публикуется один раз, уже после записи id: опрос строки не
        обгоняет WARN. Уже сохранённый id событие не отменяет.
        """
        if done.cancelled():
            return
        try:
            exc = done.exception()
        except asyncio.CancelledError:
            return
        if exc is not None:
            return
        change_id = envelope_change_id(done.result())
        if change_id:
            self._mutating_result[int(step_id)] = change_id
        if task_id not in self._shield_orphaned:
            return
        # Уже записанный id не отменяет событие: WARN всё равно один, с этим id.
        resolved = change_id or self._stored_change_id(task_id, step_id)
        if not resolved:
            return
        self._spawn(self._announce_partial_once(task_id, session_id, step_id, index, resolved))

    async def _pause_after_shield(
        self,
        task_id: int,
        session_id: str,
        plan: list[PlanStep],
        position: int,
    ) -> None:
        self._shield_orphaned.discard(task_id)
        current = self._status(task_id)
        if current in TERMINAL_STATUSES or current is None:
            return
        await self._checkpoint(task_id, session_id, plan, position)
        if current is TaskStatus.executing:
            await self._set_status(task_id, TaskStatus.paused, session_id=session_id)

    def _claim_step(self, step_id: int) -> bool:
        if step_id in self._step_recorded:
            return False
        self._step_recorded.add(step_id)
        return True

    def list_partial_change_ids(self, task_id: int) -> list[str]:
        """change_id шагов со статусом ``partial``. Отката в этом раунде нет."""
        found: list[str] = []
        for row in self.tasks.list_steps(task_id):
            if str(row.get("status")) != "partial":
                continue
            raw = row.get("change_id")
            if raw:
                found.append(str(raw))
        return found

    def _stored_change_id(self, task_id: int, step_id: int) -> str | None:
        for row in self.tasks.list_steps(task_id):
            if int(row["id"]) != int(step_id):
                continue
            raw = row.get("change_id")
            return str(raw) if raw else None
        return None

    async def _mark_mutating_partial(
        self,
        task_id: int,
        session_id: str,
        plan: list[PlanStep],
        index: int,
        started: list[tuple[PlanStep, int]],
    ) -> None:
        """Отмена mutating-шага: чекпоинт и waiting_approval.

        WARN уходит из ``_announce_partial_once``, когда id уже известен.
        Пустой ``change_ids`` отсюда не публикуется: он обгонял поздний id.
        """
        known: list[str] = []
        for done_step, step_id in started:
            if not done_step.mutating:
                await write_on_loop(self.tasks.finish_step, step_id, "cancelled", None, 0.0)
                continue
            existing = self._stored_change_id(task_id, step_id) or self._mutating_result.get(int(step_id))
            if existing:
                known.append(existing)
                await self._announce_partial_once(task_id, session_id, step_id, index, existing)
            else:
                # partial без id: resume не повторяет запись. id и WARN — когда inner кончится.
                await write_on_loop(self.tasks.finish_step, step_id, "partial", None, 0.0)
        await self._checkpoint(task_id, session_id, plan, index)
        logger.warning("orch partial_write task {} change_ids {}", task_id, known)
        current = self._status(task_id)
        if current not in TERMINAL_STATUSES and current is not TaskStatus.waiting_approval:
            await self._set_status(task_id, TaskStatus.waiting_approval, session_id=session_id)
            self._block_reason[task_id] = "partial_write"

    async def _announce_partial_once(
        self,
        task_id: int,
        session_id: str,
        step_id: int,
        index: int,
        change_id: str | None,
    ) -> None:
        """Одно ``partial_write`` с id. Строка получает id только после события."""
        key = (int(task_id), int(step_id))
        if key in self._partial_announced:
            return
        resolved = (
            change_id or self._mutating_result.get(int(step_id)) or self._stored_change_id(task_id, step_id)
        )
        if not resolved:
            return
        self._partial_announced.add(key)
        await self.bus.publish(
            session_id,
            "partial_write",
            {
                "level": "WARN",
                "change_ids": [resolved],
                "position": index,
                "task_id": task_id,
            },
            task_id=task_id,
        )
        row = next(
            (item for item in self.tasks.list_steps(task_id) if int(item["id"]) == int(step_id)),
            None,
        )
        status = str(row.get("status")) if row is not None else "running"
        if status != "done":
            cost = float((row or {}).get("cost") or 0)
            await write_on_loop(self.tasks.finish_step, int(step_id), "partial", resolved, cost)
        bucket = self._change_ids.setdefault(task_id, [])
        if resolved not in bucket:
            bucket.append(resolved)

    async def _hold_executor_approval(
        self,
        task_id: int,
        session_id: str,
        step: PlanStep,
        step_id: int,
        plan: list[PlanStep],
        position: int,
        result: Any,
    ) -> None:
        """Исполнитель вернул ``approval_required``. Чужой ``change_id`` не пишется.

        Карточка — событие ядра ``approval_required`` с новым ``orch-appr-*``.
        ``ApprovalRequired`` контракта r2.1 этого поля не несёт.
        """
        if not self._claim_step(step_id):
            return
        await self._checkpoint(task_id, session_id, plan, position)
        goal = str((self.tasks.get(task_id) or {}).get("goal") or "")
        extra: dict[str, Any] = {
            **await self._card_extra(task_id, step, goal),
            "action": step.action,
            "source": "executor",
        }
        warnings = envelope_warnings(result)
        if warnings:
            extra["warnings"] = [*extra.get("warnings", []), *warnings]
        self._block_reason[task_id] = "approval_required"
        await self._escalate(task_id, session_id, "approval_required", extra=extra)
        minted = self._appr_open.get(task_id)
        await write_on_loop(self.tasks.finish_step, step_id, "waiting_approval", minted, 0.0)

    def _write_step_done(
        self,
        task_id: int,
        step: PlanStep,
        step_id: int,
        result: Any,
        position: int | None = None,
    ) -> tuple[str | None, str | None]:
        """Записать шаг. ``(problem, change_id)``: problem — шаг не ``done``.

        Повтор после «database is locked» безопасен: core_id снимается после записи.
        ``position`` берёт решение карточки этого шага, если оно запомнено за позицией.
        """
        ok = envelope_status(result) == "ok"
        raw_cost = result.get("cost") if isinstance(result, dict) else getattr(result, "cost", 0)
        cost = float(raw_cost or 0)
        gated = metadata_call_gate(step.action, self.actions) is not None
        decided_key = (task_id, step.action)
        core_id = self._decided_change_id.get(decided_key) if gated else None
        own = self._step_grant.get((task_id, position)) if position is not None else None
        if gated and own is not None and own[0]:
            core_id = own[0]
        problem = "unapproved_action" if ok and gated and not core_id else None
        if problem is None and ok:
            problem = r21_result_problem(result)
        if problem:
            kept = envelope_change_id(result)
            self.tasks.finish_step(step_id, problem, kept, cost)
            if gated:
                self._decided_change_id.pop(decided_key, None)
                if position is not None:
                    self._step_grant.pop((task_id, position), None)
            if kept:
                bucket = self._change_ids.setdefault(task_id, [])
                if str(kept) not in bucket:
                    bucket.append(str(kept))
            self._charge_step_cost(task_id, step, cost)
            return problem, kept
        change_id = (core_id or envelope_change_id(result)) if ok else None
        self.tasks.finish_step(step_id, "done" if ok else "failed", change_id, cost)
        if gated:
            self._decided_change_id.pop(decided_key, None)
            if position is not None:
                self._step_grant.pop((task_id, position), None)
        if ok and change_id:
            bucket = self._change_ids.setdefault(task_id, [])
            if str(change_id) not in bucket:
                bucket.append(str(change_id))
        if ok and step.paid:
            self._paid_done.setdefault(task_id, set()).add(paid_step_identity(step.action, step.params))
        self._charge_step_cost(task_id, step, cost)
        return None, change_id

    def _charge_step_cost(self, task_id: int, step: PlanStep, cost: float) -> None:
        """Деньги шага пишутся и когда шаг не ``done``: лимит считает факт."""
        if not cost:
            return
        if step.paid:
            self.tasks.add_spend(task_id, actions=cost)
            return
        self.tasks.add_spend(task_id, internal=cost)

    async def _run_parallel(self, session_id: str, steps: list[PlanStep], ctx: dict[str, Any]) -> list[Any]:
        return await self._cancellable(session_id, run_many(self.executor, steps, ctx))

    async def _validate(
        self,
        task_id: int,
        session_id: str,
        step: PlanStep,
        phase: str,
        index: int | None = None,
    ) -> tuple[str, ValidatorResult | None]:
        project_id = self._task_project_id(task_id) or _step_project(step)
        if project_id is None:
            if _step_requires_project(step, self.actions):
                await self._apply_block(task_id, session_id, ApprovalReason.missing_project_id.value)
                return "project", None
            return "ok", None
        names = (
            ("no_deletions", "plan_sanity", "spend_guard")
            if phase == "after"
            else ("plan_sanity", "no_deletions")
        )
        payload: dict[str, Any] = {"action": step.action, "phase": phase, "project_id": project_id}
        if index is not None:
            granted = self._executor_approval(task_id, index, step)
            if granted is not None:
                payload["approval"] = granted
        for name in names:
            result = await run_validator(
                self.validators,
                name,
                project_id,
                payload,
                run_id=orch_run_id(task_id=task_id),
                task_id=task_id,
            )
            outcome = await self._publish_validator(task_id, session_id, result)
            if outcome == "missing_run":
                await self._fail_closed(task_id, session_id, "missing_run_id")
                return outcome, result
            if outcome != "ok":
                return outcome, result
        return "ok", None

    async def _dry_run(self, task_id: int, session_id: str, plan: list[PlanStep]) -> str:
        project_id = self._task_project_id(task_id)
        if project_id is None:
            # «Вся фабрика»: каждый изменяющий шаг назвал свой проект (иначе _screen_plan
            # уже остановил план) — проверяем по проекту только его шаги.
            groups: dict[int, list[PlanStep]] = {}
            for step in plan:
                pid = _step_project(step)
                if pid is not None:
                    groups.setdefault(pid, []).append(step)
            for pid, steps in groups.items():
                outcome = await self._dry_run_project(task_id, session_id, steps, pid)
                if outcome != "ok":
                    return outcome
            return "ok"
        return await self._dry_run_project(task_id, session_id, plan, project_id)

    async def _dry_run_project(
        self, task_id: int, session_id: str, plan: list[PlanStep], project_id: int
    ) -> str:
        payload = {
            VALIDATOR_CONTEXT_DRY_RUN: True,
            "project_id": project_id,
            "plan": plan_without_client_money(plan),
        }
        for name in _DRY_RUN:
            result = await run_validator(
                self.validators,
                name,
                project_id,
                payload,
                run_id=orch_run_id(task_id=task_id),
                task_id=task_id,
            )
            outcome = await self._publish_validator(task_id, session_id, result)
            if outcome == "missing_run":
                await self._fail_closed(task_id, session_id, "missing_run_id")
                return "missing_run"
            if outcome == "budget":
                await self._halt_for_validator_budget(task_id, session_id, plan, 0, result)
                return "budget"
            if outcome == "blocked":
                if name == "plan_sanity":
                    self._plan_feedback[task_id] = _sanity_feedback(plan, result)
                return "blocked"
        return "ok"

    async def _publish_validator(self, task_id: int, session_id: str, result: ValidatorResult) -> str:
        await self.bus.publish(
            session_id,
            "validator_result",
            result.model_dump(by_alias=True),
            task_id=task_id,
        )
        if bool(result.passed) or not bool(result.blocking):
            return "ok"
        if validator_missing_run_id(result):
            return "missing_run"
        if validator_budget_exhausted(result):
            return "budget"
        return "blocked"

    async def _halt_for_validator_budget(
        self,
        task_id: int,
        session_id: str,
        plan: list[PlanStep],
        position: int,
        result: ValidatorResult | None,
    ) -> None:
        details = getattr(result, "details", None)
        payload = dict(details) if isinstance(details, dict) else {"details": details}
        approval = ApprovalEvent(
            task_id=task_id,
            reason=ApprovalReason.budget_exhausted.value,
            payload=payload,
        )
        self._budget_stop.add(task_id)
        await self._checkpoint(task_id, session_id, plan, position)
        extra = approval.model_dump()
        extra.pop("reason", None)
        await self._apply_block(task_id, session_id, approval.reason, extra=extra)

    async def _apply_block(
        self,
        task_id: int,
        session_id: str,
        reason: str,
        extra: dict[str, Any] | None = None,
    ) -> None:
        """Статус блокировки берётся из ``BLOCK_OUTCOMES``, не из ветки вызова."""
        self._block_reason[task_id] = reason
        self._persist_block_reason(task_id, session_id)
        outcome = block_status(reason)
        if outcome is TaskStatus.waiting_approval:
            await self._escalate(task_id, session_id, reason, extra=extra)
            return
        if outcome is TaskStatus.cancelled:
            current = self._status(task_id)
            if current not in TERMINAL_STATUSES and current is not None:
                await self._set_status(task_id, TaskStatus.cancelled, session_id=session_id)
            return
        await self._fail_closed(task_id, session_id, reason)

    def _persist_block_reason(self, task_id: int, session_id: str) -> None:
        reason = self._block_reason.get(task_id)
        if reason not in _PERSISTED_BLOCKS:
            return
        data = self.checkpoints.load_latest(task_id)
        if data is None:
            return
        visible, mark = _unpack_approved(data.context or "")
        packed = _pack_approved(visible, mark, reason)
        if packed == (data.context or ""):
            return
        self.checkpoints.save(task_id, session_id, data.model_copy(update={"context": packed}))

    async def _fail_validation(self, task_id: int, session_id: str) -> None:
        await self._apply_block(task_id, session_id, "validator_blocked")

    async def _fail_closed(self, task_id: int, session_id: str, error: str) -> None:
        current = self._status(task_id)
        if current in TERMINAL_STATUSES or current is TaskStatus.waiting_approval:
            return
        await self._set_status(task_id, TaskStatus.failed, session_id=session_id, error=error)
        await self.bus.publish(session_id, "task_failed", {"error": error}, task_id=task_id)

    async def _settle_stopped(self, task_id: int, session_id: str) -> None:
        """Ветка None оставляет waiting_approval или терминальный статус."""
        status = self._status(task_id)
        if status is None or status in TERMINAL_STATUSES or status is TaskStatus.waiting_approval:
            return
        if task_id in self._budget_stop and can_transition(
            status, block_status(ApprovalReason.budget_exhausted.value)
        ):
            await self._apply_block(task_id, session_id, ApprovalReason.budget_exhausted.value)
            return
        if can_transition(status, TaskStatus.failed):
            await self._fail_closed(task_id, session_id, "plan_stopped")

    def _task_project_id(self, task_id: int) -> int | None:
        raw = (self.tasks.get(task_id) or {}).get("project_id")
        if raw is None:
            return None
        try:
            value = int(raw)
        except (TypeError, ValueError):
            return None
        if value <= 0:
            return None
        return value

    async def _record(
        self,
        task_id: int,
        session_id: str,
        step: PlanStep,
        step_id: int,
        result: Any,
        position: int,
    ) -> bool:
        """True — шаг успешен или уже записан. ``ok=False`` не done и не paid-retry."""
        if not self._claim_step(step_id):
            return True
        note = self._note_step(task_id, step, result)
        problem, change_id = await write_on_loop(
            self._write_step_done, task_id, step, step_id, result, position, what=f"task {task_id} step done"
        )
        if problem:
            await self.bus.publish(
                session_id,
                problem,
                {
                    "position": position,
                    "action": step.action,
                    "status": problem,
                    "ok": False,
                },
                task_id=task_id,
            )
            current = self._status(task_id)
            if current not in TERMINAL_STATUSES and current is not TaskStatus.waiting_approval:
                await self._set_status(task_id, TaskStatus.failed, session_id=session_id, error=problem)
                await self.bus.publish(
                    session_id,
                    "task_failed",
                    {"error": problem, "action": step.action},
                    task_id=task_id,
                )
            return False
        await self.bus.publish(
            session_id,
            "step_finished",
            {
                "position": position,
                "action": step.action,
                "ok": envelope_status(result) == "ok",
                "change_id": change_id,
                "warnings": envelope_warnings(result),
                "skipped": envelope_skipped(result),
                "summary": task_summary.step_summary(note),
            },
            task_id=task_id,
        )
        return envelope_status(result) == "ok"

    def _note_step(self, task_id: int, step: PlanStep, result: Any) -> task_summary.StepNote | None:
        try:
            note = task_summary.note_from_result(
                step.action, result, readonly=step_is_readonly(step, self.actions)
            )
            note.project_id = _step_project(step)
        except Exception:
            logger.exception("orch step note failed for {}", step.action)
            return None
        self._notes.setdefault(task_id, []).append(note)
        return note

    async def _announce_result(
        self,
        task_id: int,
        session_id: str,
        status: TaskStatus,
        error: str | None,
    ) -> None:
        if task_id in self._announced:
            return
        self._announced.add(task_id)
        row = self.tasks.get(task_id) or {}
        facts = task_summary.TaskFacts(
            goal=str(row.get("goal") or ""),
            status=status.value,
            error=error or self._block_reason.get(task_id),
            detail=self._fail_detail.pop(task_id, ""),
            steps=self._notes.pop(task_id, []),
            answered=task_id in self._code_answers,
        )
        self._code_answers.discard(task_id)
        token = _LLM_TASK.set(int(task_id))
        try:
            text = await self.summarizer.summarize(
                facts, run_id=orch_run_id(task_id=task_id), task_id=task_id
            )
        except Exception:
            logger.exception("orch summary failed for task {}", task_id)
            text = task_summary.deterministic_text(facts)
        finally:
            _LLM_TASK.reset(token)
        text = str(text or "").strip()
        if not text:
            return
        try:
            if await asyncio.to_thread(task_summary.has_prior_text, session_id, task_id):
                text = "\n\n" + text
            await asyncio.to_thread(task_summary.store_message, session_id, task_id, text.strip())
        except Exception:
            logger.exception("orch summary store failed for task {}", task_id)
        await self.bus.publish(
            session_id,
            "message",
            task_summary.payload(task_id, text, status.value),
            task_id=task_id,
        )

    def _skip_reason(
        self,
        task_id: int,
        step: PlanStep,
        plan: list[PlanStep],
        index: int,
    ) -> str | None:
        if self._change_already_applied(task_id, step, plan, index):
            return "change_already_applied"
        if step.paid and paid_step_identity(step.action, step.params) in self._paid_done.get(task_id, set()):
            return "paid_retry"
        return None

    def _change_already_applied(
        self,
        task_id: int,
        step: PlanStep,
        plan: list[PlanStep],
        index: int,
    ) -> bool:
        """Повтор шага, у которого в ``orch_steps.change_id`` уже есть сдача.

        Параметр ``_change_id`` плана не источник. Считаются done, cancelled
        и ``skipped_without_partial`` с непустым change_id и теми же
        публичными params. ``partial`` сюда не входит: его решает человек
        через ``partial_write``.
        """
        ident = _public_params(step.params)
        prior = sum(
            1 for item in plan[:index] if item.action == step.action and _public_params(item.params) == ident
        )
        done = 0
        for row in self._rows_for_step(task_id, step):
            if str(row.get("status")) not in {"done", "cancelled", "skipped_without_partial"}:
                continue
            if not row.get("change_id"):
                continue
            done += 1
        return done > prior

    def _rows_for_step(self, task_id: int, step: PlanStep) -> list[dict[str, Any]]:
        ident = _public_params(step.params)
        found: list[dict[str, Any]] = []
        for row in self.tasks.list_steps(task_id):
            if str(row.get("action_name") or "") != step.action:
                continue
            try:
                stored = json.loads(row.get("params_json") or "{}")
            except json.JSONDecodeError:
                stored = {}
            if isinstance(stored, dict) and _public_params(stored) == ident:
                found.append(row)
        return found

    def _open_partial_row(
        self,
        task_id: int,
        step: PlanStep,
        plan: list[PlanStep],
        index: int,
    ) -> dict[str, Any] | None:
        """Строка ``partial`` этого шага. Повторный execute по ней не идёт."""
        ident = _public_params(step.params)
        prior = sum(
            1 for item in plan[:index] if item.action == step.action and _public_params(item.params) == ident
        )
        rows = [row for row in self._rows_for_step(task_id, step) if str(row.get("status")) == "partial"]
        if len(rows) > prior:
            return rows[prior]
        return None

    def _next_step_position(self, task_id: int) -> int:
        rows = self.tasks.list_steps(task_id)
        if not rows:
            return 0
        return max(int(row["position"]) for row in rows) + 1

    async def _capture_unknown_paid(
        self,
        task_id: int,
        session_id: str,
        started: list[tuple[PlanStep, int]],
        results: list[Any],
        plan: list[PlanStep],
        index: int,
    ) -> None:
        """Исход платного шага неизвестен, но сдача и стоимость уже есть."""
        noted = False
        for (done_step, step_id), result in zip(started, results, strict=True):
            if not done_step.paid or isinstance(result, BaseException):
                continue
            cost = float(getattr(result, "cost", 0) or 0)
            raw_id = getattr(result, "change_id", None)
            change_id = str(raw_id) if raw_id else None
            await write_on_loop(self.tasks.note_running_outcome, step_id, change_id, cost)
            if change_id:
                bucket = self._change_ids.setdefault(task_id, [])
                if change_id not in bucket:
                    bucket.append(change_id)
            if cost:
                await write_on_loop(self.tasks.add_spend, task_id, actions=cost)
            noted = True
        if noted:
            await self._checkpoint(task_id, session_id, plan, index)

    def _accept_confirmed_paid(self, task_id: int, plan: list[PlanStep], position: int) -> bool:
        """Закрыть running-строку с подтверждённым change_id как done."""
        if position < 0 or position >= len(plan):
            return False
        step = plan[position]
        for row in self.tasks.list_steps(task_id):
            if str(row.get("status")) != "running" or not row.get("paid"):
                continue
            if str(row.get("action_name") or "") != step.action:
                continue
            change_id = row.get("change_id")
            if not change_id:
                return False
            text = str(change_id)
            self.tasks.finish_step(int(row["id"]), "done", text, float(row.get("cost") or 0))
            self._paid_done.setdefault(task_id, set()).add(paid_step_identity(step.action, step.params))
            bucket = self._change_ids.setdefault(task_id, [])
            if text not in bucket:
                bucket.append(text)
            return True
        return False

    def _paid_spend_fills_budget(self, task_id: int) -> bool:
        """Уже списанная стоимость платных шагов заняла потолок прогноза."""
        limit = server_forecast_limit()
        if ceiling_blocks(limit):
            return True
        return self._spend(task_id).actions >= float(limit)

    async def _deny_paid_outcome(self, session_id: str, task_id: int) -> None:
        """Отказ по неизвестному платному исходу: задача cancelled, строка остаётся."""
        self._paid_outcome_hold.discard(task_id)
        for row in self.tasks.list_steps(task_id):
            if str(row.get("status")) != "running":
                continue
            change_id = row.get("change_id")
            await write_on_loop(
                self.tasks.finish_step,
                int(row["id"]),
                "cancelled",
                str(change_id) if change_id else None,
                float(row.get("cost") or 0),
                what="finish_step",
            )
        current = self._status(task_id)
        if current is not None and current not in TERMINAL_STATUSES:
            await self._set_status(task_id, TaskStatus.cancelled, session_id=session_id)

    async def _mark_paid_outcome_unknown(self, session_id: str, task_id: int) -> None:
        """Kill или interrupt во время платного шага: исход неизвестен, отката нет.

        Спавн из ``request_cancel`` и повторный вызов после ``shield`` не
        должны оба дойти до ``waiting_approval -> waiting_approval``.
        """
        if task_id not in self._paid_outcome_hold:
            return
        if task_id in self._paid_unknown_marking:
            while task_id in self._paid_unknown_marking:
                await asyncio.sleep(0)
            return
        self._paid_unknown_marking.add(task_id)
        try:
            status = self._status(task_id)
            if status is None or status in TERMINAL_STATUSES or status is TaskStatus.waiting_approval:
                return
            action = ""
            for row in self.tasks.list_steps(task_id):
                if str(row.get("status")) == "running" and row.get("paid"):
                    action = str(row.get("action_name") or "")
                    break
            await self._apply_block(
                task_id,
                session_id,
                "paid_outcome_unknown",
                extra={"action": action} if action else None,
            )
        finally:
            self._paid_unknown_marking.discard(task_id)

    async def _hold_paid_unknown(self, task_id: int, session_id: str, step: PlanStep) -> None:
        await self._apply_block(
            task_id,
            session_id,
            "paid_unknown_outcome",
            extra={"action": step.action},
        )

    async def _replace_waiting_reason(
        self,
        task_id: int,
        session_id: str,
        reason: str,
        extra: dict[str, Any] | None = None,
    ) -> None:
        """Задача уже ``waiting_approval``: новая причина без повторного перехода."""
        self._block_reason[task_id] = reason
        self._persist_block_reason(task_id, session_id)
        payload: dict[str, Any] = {"reason": reason, "task_id": task_id}
        if extra:
            payload.update(extra)
        await self.publish_approval_required(session_id, payload, task_id=task_id)

    def _remember_paid(self, task_id: int) -> None:
        found = self._paid_done.setdefault(task_id, set())
        for row in self.tasks.list_steps(task_id):
            if str(row.get("status")) != "done" or not row.get("paid"):
                continue
            try:
                params = json.loads(row.get("params_json") or "{}")
            except json.JSONDecodeError:
                params = {}
            if not isinstance(params, dict):
                params = {}
            found.add(paid_step_identity(str(row.get("action_name") or ""), params))

    async def _checkpoint(
        self,
        task_id: int,
        session_id: str,
        plan: list[PlanStep],
        position: int,
    ) -> None:
        data = CheckpointData(
            plan=list(plan),
            position=position,
            change_ids=list(self._change_ids.get(task_id, [])),
            spend=self._spend(task_id),
            context=self._checkpoint_context(task_id),
        )
        await write_on_loop(self.tasks.mirror_progress, task_id, plan, position, data.spend)
        await asyncio.to_thread(self.checkpoints.save, task_id, session_id, data)
        await self.bus.publish(
            session_id,
            "checkpoint",
            data.model_dump(),
            task_id=task_id,
        )

    async def _hold_for_approval(
        self,
        task_id: int,
        session_id: str,
        plan: list[PlanStep],
        index: int,
    ) -> bool:
        reason = self._approval.pop(session_id, None)
        if reason is None:
            return False
        await self._checkpoint(task_id, session_id, plan, index)
        await self._escalate(task_id, session_id, reason)
        return True

    def _guard_change_id(
        self,
        task_id: int,
        session_id: str,
        change_id: str | None,
        *,
        retry_grant: bool,
        decision: ApprovalDecision | str | None,
    ) -> None:
        """404 — чужой или неизвестный id. 409 — уже решён или не текущий.

        Проверка владельца идёт раньше 409, чтобы id другой задачи не
        выглядел просто «не текущим». ``retry_grant`` после ``retry_step``
        остаётся одноразовым допуском.
        """
        key = int(task_id)
        button = _coerce_decision(decision) is not None
        if not change_id:
            open_id = self._appr_open.get(key)
            if (
                button
                and not retry_grant
                and open_id is not None
                and open_id in self._appr_decided
                and not self._retry_choice_open(key, open_id, decision)
            ):
                raise ApprovalGrantError(409, "change_id already decided")
            return
        owner = self._appr_owner.get(change_id)
        if owner is None:
            raise ApprovalGrantError(404, "unknown change_id")
        if owner != (key, session_id):
            raise ApprovalGrantError(404, "foreign change_id")
        if change_id in self._appr_decided:
            label = self._appr_decided[change_id]
            if retry_grant and label == ApprovalDecision.retry_step.value:
                return
            if self._retry_choice_open(key, change_id, decision):
                return
            raise ApprovalGrantError(409, "change_id already decided")
        if self._appr_open.get(key) != change_id:
            raise ApprovalGrantError(409, "change_id is not current")

    def _retry_choice_open(
        self,
        task_id: int,
        change_id: str,
        decision: ApprovalDecision | str | None,
    ) -> bool:
        """approve/deny поверх retry_step, пока одноразовый ключ не потрачен."""
        if self._appr_decided.get(change_id) != ApprovalDecision.retry_step.value:
            return False
        if self._retry_grant_is_spent(int(task_id)):
            return False
        return _coerce_decision(decision) in {ApprovalDecision.approve, ApprovalDecision.deny}

    async def publish_approval_required(
        self,
        session_id: str,
        payload: dict[str, Any],
        *,
        task_id: int | None,
        reuse: bool = False,
    ) -> None:
        """Событие ``approval_required`` с серверным ``change_id``.

        Поле ``change_id`` из payload клиента снимается. ``reuse`` оставляет
        уже открытый id того же ожидания (повтор прогноза, не новое одобрение).
        """
        labels = await self._card_labels(task_id)
        body = await self._assign_change_id(session_id, task_id, payload, reuse=reuse)
        body = self._decorate_approval(body, task_id, labels)
        await self.bus.publish(session_id, "approval_required", body, task_id=task_id)
        await self._persist_appr_mark(task_id, session_id)

    async def _refuse_approved_plan(self, task_id: int, session_id: str, reason: str) -> None:
        self._block_reason[task_id] = reason
        self._fail_detail[task_id] = _HUMAN_PLAN_BLOCKED.format(why=_CHECK_RU.get(reason, "проверка плана"))
        if self._status(task_id) is TaskStatus.waiting_approval:
            await self._set_status(task_id, TaskStatus.executing, session_id=session_id)
        await self._fail_closed(task_id, session_id, reason)

    async def _card_labels(self, task_id: int | None, steps: list[PlanStep] | None = None) -> dict[str, str]:
        loader = self.card_meta
        if loader is None or task_id is None:
            return {}
        plan = steps if steps is not None else self._card_plan(int(task_id))
        keys = approval_steps.plan_node_keys(plan)
        project_id = approval_steps.plan_project_id(plan) or self._task_project_id(int(task_id))
        if not keys or project_id is None:
            return {}
        try:
            meta = loader(project_id)
            if inspect.isawaitable(meta):
                meta = await meta
        except Exception:
            logger.exception("orch card labels failed for task {}", task_id)
            return {}
        return approval_steps.node_labels(meta, keys)

    def _decorate_approval(
        self,
        body: dict[str, Any],
        task_id: int | None,
        labels: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Явные кнопки. Для paid_outcome_unknown ещё change_id шага из чекпоинта.

        ``change_id`` карточки (``orch-appr-*``) не затирается: по нему grant.
        """
        card = body.get("change_id")
        action = body.get("action")
        if isinstance(card, str) and isinstance(action, str) and action:
            self._card_action[card] = action
            cc = body.get("cc_change_id")
            if isinstance(cc, str) and task_id is not None:
                self._cc_change_id[(int(task_id), action)] = cc
        if task_id is not None:
            card_plan = self._card_plan(int(task_id))
            fresh = not isinstance(body.get("steps"), list)
            body = approval_steps.enrich(body, card_plan, self.actions, labels)
            if fresh and isinstance(body.get("steps"), list) and len(body["steps"]) == len(card_plan):
                self._settings_cards(int(task_id), card_plan, body["steps"])
            self._settings_card_note(int(task_id), body)
        reason = str(body.get("reason") or "")
        if task_id is not None and reason:
            self._card_reason[int(task_id)] = reason
        if reason in _PAID_OUTCOME_REASONS:
            body["actions"] = ["approve", "retry_step", "deny"]
            if task_id is not None:
                step_change_id = self._paid_step_change_id(int(task_id))
                if step_change_id:
                    body["step_change_id"] = step_change_id
            return body
        body["actions"] = ["approve"]
        return body

    def _card_plan(self, task_id: int) -> list[PlanStep]:
        plan = self._stored_plan(task_id)
        if plan:
            return plan
        data = self.checkpoints.load_latest(task_id)
        return list(data.plan) if data is not None else []

    def _paid_step_change_id(self, task_id: int) -> str | None:
        for row in self.tasks.list_steps(task_id):
            if str(row.get("status")) != "running" or not row.get("paid"):
                continue
            if row.get("change_id"):
                return str(row["change_id"])
            try:
                params = json.loads(row.get("params_json") or "{}")
            except json.JSONDecodeError:
                params = {}
            planned = params.get("_change_id") if isinstance(params, dict) else None
            if isinstance(planned, str) and planned:
                return planned
        data = self.checkpoints.load_latest(task_id)
        if data is None or not data.change_ids:
            return None
        return str(data.change_ids[-1])

    async def _assign_change_id(
        self,
        session_id: str,
        task_id: int | None,
        payload: dict[str, Any],
        *,
        reuse: bool,
    ) -> dict[str, Any]:
        body = dict(payload)
        body.pop("change_id", None)
        key = _appr_key(task_id)
        if key not in self._appr_loaded:
            await self._load_appr_state(session_id, task_id)
        current = self._appr_open.get(key)
        if reuse and current:
            body["change_id"] = current
            return body
        body["change_id"] = self._mint_change_id(key, session_id)
        return body

    def _mint_change_id(self, key: int, session_id: str) -> str:
        number = self._appr_seq.get(key, 0) + 1
        self._appr_seq[key] = number
        change_id = f"orch-appr-{key}-{number}"
        self._appr_owner[change_id] = (key, session_id)
        self._appr_open[key] = change_id
        self._appr_loaded.add(key)
        return change_id

    async def _emit_decision(
        self,
        task_id: int,
        session_id: str,
        decision: ApprovalDecision | None,
    ) -> None:
        key = _appr_key(task_id)
        if key not in self._appr_loaded:
            await self._load_appr_state(session_id, task_id)
        change_id = self._appr_open.get(key)
        label = decision.value if decision is not None else None
        if change_id:
            self._appr_decided[change_id] = label
            self._remember_authorizing_decision(key, change_id, label)
            if label != ApprovalDecision.retry_step.value:
                self._appr_open[key] = None
        await self.bus.publish(
            session_id,
            "approval_decided",
            {"change_id": change_id, "decision": label, "task_id": task_id},
            task_id=task_id,
        )
        await self._persist_appr_mark(task_id, session_id)

    async def _load_appr_state(self, session_id: str, task_id: int | None) -> None:
        key = _appr_key(task_id)
        if key in self._appr_loaded:
            return
        self._clear_appr_key(key)
        events = await self.bus.replay_from(session_id, 0)
        found = self._absorb_appr_events(key, session_id, events)
        data = None
        if task_id:
            data = await asyncio.to_thread(self.checkpoints.load_latest, int(task_id))
        if data is not None and task_id and not self._context.get(int(task_id), ""):
            self._context[int(task_id)] = _split_appr_context(data.context)[0]
        if not found and data is not None:
            _visible, packed = _split_appr_context(data.context)
            self._merge_appr_pack(key, session_id, packed)
        if data is not None and task_id:
            _visible, packed = _split_appr_context(data.context or "")
            self._restore_retry_armed(int(task_id), packed)
        self._appr_loaded.add(key)

    def _restore_retry_armed(self, task_id: int, packed: dict[str, Any] | None) -> None:
        if not isinstance(packed, dict):
            return
        spent = packed.get("retry_spent")
        if isinstance(spent, list):
            for item in spent:
                if isinstance(item, str) and item:
                    self._retry_spent.add((int(task_id), item))
        armed = packed.get("retry_armed")
        if not isinstance(armed, str) or not armed:
            return
        if (int(task_id), armed) in self._retry_spent:
            return
        if any(stored == task_id for stored, _key in self._retry_spent):
            return
        self._retry_armed[task_id] = armed

    def _absorb_appr_events(self, key: int, session_id: str, events: list[Any]) -> bool:
        found = self._absorb_appr_list(key, session_id, events)
        open_id = self._appr_open.get(key)
        if key and open_id:
            reason = next(
                (
                    event.payload.get("reason")
                    for event in reversed(events)
                    if event.type == "approval_required"
                    and event.task_id == key
                    and isinstance(event.payload, dict)
                    and event.payload.get("change_id") == open_id
                ),
                None,
            )
            if isinstance(reason, str) and reason:
                self._card_reason[key] = reason
                if reason in _PLAN_LEVEL_CARDS:
                    self._block_reason.setdefault(key, reason)
        return found

    def _absorb_appr_list(self, key: int, session_id: str, events: list[Any]) -> bool:
        found = False
        for event in events:
            if event.type not in {"approval_required", "approval_decided"}:
                continue
            if key == 0:
                if event.task_id is not None:
                    continue
            elif event.task_id != key:
                continue
            payload = event.payload if isinstance(event.payload, dict) else {}
            change_id = _server_change_id(key, payload.get("change_id"))
            if event.type == "approval_required":
                if change_id is None:
                    continue
                found = True
                self._appr_owner[change_id] = (key, session_id)
                self._appr_open[key] = change_id
                self._note_appr_seq(key, change_id)
                action = payload.get("action")
                if isinstance(action, str) and action:
                    self._card_action[change_id] = action
                self._absorb_group_card(change_id, payload.get("group"))
                reason = payload.get("reason")
                if key and reason == "paid_outcome_unknown" and key not in self._block_reason:
                    self._block_reason[key] = "paid_outcome_unknown"
                    self._paid_outcome_hold.add(key)
                continue
            if change_id is None:
                continue
            label = payload.get("decision")
            stored = label if isinstance(label, str) else None
            self._appr_decided[change_id] = stored
            self._remember_authorizing_decision(key, change_id, stored)
            if (
                self._appr_decided[change_id] != ApprovalDecision.retry_step.value
                and self._appr_open.get(key) == change_id
            ):
                self._appr_open[key] = None
        return found

    def _absorb_group_card(self, change_id: str, group: Any) -> None:
        """Групповая карточка из журнала событий: после рестарта grant одобряет те же шаги."""
        if not isinstance(group, dict):
            return
        positions = group.get("positions")
        mark = group.get("fingerprint")
        if not isinstance(mark, str) or not mark or not isinstance(positions, list):
            return
        if not positions or not all(isinstance(at, int) and not isinstance(at, bool) for at in positions):
            return
        self._group_cards[change_id] = (tuple(positions), mark)

    def _note_appr_seq(self, key: int, change_id: str) -> None:
        match = _APPR_ID.fullmatch(change_id)
        if match is None:
            return
        number = int(match.group(2))
        if number > self._appr_seq.get(key, 0):
            self._appr_seq[key] = number

    def _decision_authorizes(self, label: str | None) -> bool:
        """Пустой grant и approve/retry_step. deny и чужая причина не открывают шаг."""
        return label in {None, ApprovalDecision.approve.value, ApprovalDecision.retry_step.value}

    def _remember_authorizing_decision(self, key: int, change_id: str, label: str | None) -> None:
        if not self._decision_authorizes(label):
            return
        action = self._card_action.get(change_id)
        if not action:
            return
        self._decided_change_id[(key, action)] = change_id

    def _clear_appr_key(self, key: int) -> None:
        self._card_reason.pop(key, None)
        self._appr_seq.pop(key, None)
        self._appr_open.pop(key, None)
        for pair in list(self._decided_change_id):
            if pair[0] == key:
                self._decided_change_id.pop(pair, None)
        for change_id, owner in list(self._appr_owner.items()):
            if owner[0] == key:
                self._appr_owner.pop(change_id, None)
                self._appr_decided.pop(change_id, None)
                self._card_action.pop(change_id, None)
                self._group_cards.pop(change_id, None)
        for pair in list(self._cc_change_id):
            if pair[0] == key:
                self._cc_change_id.pop(pair, None)

    def _merge_appr_pack(self, key: int, session_id: str, packed: dict[str, Any] | None) -> None:
        if not packed:
            return
        try:
            seq = int(packed.get("seq") or 0)
        except (TypeError, ValueError):
            return
        memory = self._appr_seq.get(key, 0)
        if seq < memory:
            return
        sessions = packed.get("sessions") if isinstance(packed.get("sessions"), dict) else {}
        issued = packed.get("issued") if isinstance(packed.get("issued"), list) else []
        for raw in issued:
            change_id = _server_change_id(key, raw)
            if change_id is None:
                continue
            owner_session = (
                sessions.get(change_id) if isinstance(sessions.get(change_id), str) else session_id
            )
            self._appr_owner.setdefault(change_id, (key, str(owner_session)))
            self._note_appr_seq(key, change_id)
        decided = packed.get("decided") if isinstance(packed.get("decided"), dict) else {}
        for raw, value in decided.items():
            change_id = _server_change_id(key, raw)
            if change_id is None or change_id in self._appr_decided:
                continue
            self._appr_decided[change_id] = value if isinstance(value, str) or value is None else None
        if seq > memory and "open" in packed:
            opened = _server_change_id(key, packed.get("open"))
            self._appr_open[key] = opened

    def _appr_pack_dict(self, key: int) -> dict[str, Any] | None:
        issued = [change_id for change_id, owner in self._appr_owner.items() if owner[0] == key]
        spent_keys = sorted(item for stored, item in self._retry_spent if stored == key)
        if not issued and not self._appr_seq.get(key, 0) and not spent_keys:
            return None
        state = {
            "seq": self._appr_seq.get(key, 0),
            "open": self._appr_open.get(key),
            "issued": sorted(issued),
            "decided": {
                change_id: self._appr_decided[change_id]
                for change_id in issued
                if change_id in self._appr_decided
            },
            "sessions": {change_id: self._appr_owner[change_id][1] for change_id in issued},
        }
        armed = self._retry_armed.get(key)
        if isinstance(armed, str) and armed:
            state["retry_armed"] = armed
        if spent_keys:
            state["retry_spent"] = spent_keys
        return state

    def _checkpoint_context(self, task_id: int) -> str:
        visible, _packed = _split_appr_context(self._context.get(task_id, ""))
        visible, _mark = _unpack_approved(visible)
        reason = self._block_reason.get(task_id)
        packed = _pack_unknown(
            _pack_approved(
                _strip_unknown(visible),
                self._approved.get(task_id),
                reason if reason in _PERSISTED_BLOCKS else None,
            ),
            self._unknown_step.get(task_id),
        )
        return self._context_with_appr(task_id, packed)

    def _context_with_appr(self, task_id: int, visible: str) -> str:
        visible, _packed = _split_appr_context(visible)
        state = self._appr_pack_dict(int(task_id))
        if not state:
            return visible
        return visible + _APPR_MARK + json.dumps(state, ensure_ascii=False, sort_keys=True)

    async def _persist_appr_mark(self, task_id: int | None, session_id: str) -> None:
        if not task_id:
            return
        data = await asyncio.to_thread(self.checkpoints.load_latest, int(task_id))
        if data is None:
            return
        stored, _packed = _split_appr_context(data.context or "")
        stored, stored_mark = _unpack_approved(stored)
        memory, _memory_pack = _split_appr_context(self._context.get(int(task_id), ""))
        memory, memory_mark = _unpack_approved(memory)
        base = memory or stored
        mark = self._approved.get(int(task_id)) or memory_mark or stored_mark
        reason = self._block_reason.get(int(task_id))
        packed_context = self._context_with_appr(
            int(task_id),
            _pack_approved(base, mark, reason if reason in _PERSISTED_BLOCKS else None),
        )
        if packed_context == data.context:
            return
        await asyncio.to_thread(
            self.checkpoints.save_context,
            int(task_id),
            session_id,
            packed_context,
        )

    async def _escalate(
        self,
        task_id: int,
        session_id: str,
        reason: str,
        extra: dict[str, Any] | None = None,
    ) -> None:
        current = self._status(task_id)
        payload: dict[str, Any] = {"reason": reason, "task_id": task_id}
        if extra:
            payload.update(extra)
        if reason == "paid_outcome_unknown":
            payload.setdefault("retry_armed", False)
        if current is None or not can_transition(current, TaskStatus.waiting_approval):
            await self._set_status(task_id, TaskStatus.waiting_approval, session_id=session_id)
            return
        if reason in approval_steps.PLAN_CARD_REASONS and not self._card_plan(task_id):
            self._block_reason[task_id] = "empty_plan"
            await self._fail_closed(task_id, session_id, "empty_plan")
            return
        if reason in _PLAN_LEVEL_CARDS:
            shown = _plan_fingerprint(self._card_plan(task_id))
            if self._human_plan.get(task_id) == shown:
                await self._refuse_approved_plan(task_id, session_id, reason)
                return
            self._escalated[task_id] = shown
        labels = await self._card_labels(task_id)
        body = await self._assign_change_id(session_id, task_id, payload, reuse=False)
        body = self._decorate_approval(body, task_id, labels)
        await self.bus.publish(session_id, "approval_required", body, task_id=task_id)
        # Статус пишется синхронно в начале _set_status, до следующего await:
        # подписчик события уже видит waiting_approval, а replay после статуса — событие.
        if self._status(task_id) is TaskStatus.waiting_approval:
            await self._persist_appr_mark(task_id, session_id)
            return
        await self._set_status(task_id, TaskStatus.waiting_approval, session_id=session_id)
        await self._persist_appr_mark(task_id, session_id)

    async def _set_status(
        self,
        task_id: int | None,
        status: TaskStatus,
        *,
        session_id: str,
        goal: str = "",
        error: str | None = None,
        project_id: int | None = None,
    ) -> int:
        if task_id is None:
            if status is not TaskStatus.queued:
                raise InvalidTransition("новая задача начинается со статуса queued")
            task_id = await write_on_loop(
                self.tasks.insert,
                session_id,
                goal,
                status.value,
                project_id=project_id,
                budget_s=self.status_write_budget_s,
                what="task insert",
            )
            current = None
        else:
            current = await write_on_loop(
                self._write_status,
                task_id,
                status,
                error,
                budget_s=self.status_write_budget_s,
                what=f"task {task_id} -> {status.value}",
            )
        self._unsettled.discard(task_id)
        self.history.append((task_id, status.value))
        try:
            body: dict[str, Any] = {
                "status": status.value,
                "agent": "supervisor",
                "task_id": task_id,
                "resume_allowed": self.resume_allowed(task_id),
            }
            if error and status in TERMINAL_STATUSES:
                # UI отличает «не понял» (empty_plan) и отказ человека (rejected) от прочей отмены.
                body["reason"] = error
                if error == "empty_plan" and task_id in self._code_answers:
                    # «Возвращать нечего», «этого чат не умеет»: чат ответил, это не «нужно уточнение».
                    body["reason"] = "answered"
            await self.bus.publish(session_id, "agent_status", body, task_id=task_id)
        finally:
            if status in TERMINAL_STATUSES:
                try:
                    if current is not None and current is not TaskStatus.queued:
                        await self._announce_result(task_id, session_id, status, error)
                except Exception:
                    logger.exception("orch result message failed for task {}", task_id)
                finally:
                    self._planner_meta.pop(task_id, None)
                    self._critic_meta.pop(task_id, None)
                    self._kill_stopped.pop(task_id, None)
                    await self._start_queued(session_id, task_id)
        return task_id

    def _write_status(self, task_id: int, status: TaskStatus, error: str | None) -> TaskStatus:
        current = self._status(task_id)
        if current is None or not can_transition(current, status):
            src = current.value if current is not None else "missing"
            raise InvalidTransition(f"{src} -> {status.value}")
        self.tasks.set_status(task_id, status.value, error=error)
        return current

    async def _settle_failure(
        self,
        task_id: int,
        session_id: str,
        exc: BaseException,
        error: str = "drive_failed",
        keep_held: bool = False,
    ) -> None:
        try:
            current = self._status(task_id)
            if current in TERMINAL_STATUSES or (keep_held and current in _HOLD_STATUSES):
                return
            self._fail_detail[task_id] = task_summary.explain_failure(exc)
            await self._set_status(task_id, TaskStatus.failed, session_id=session_id, error=error)
        except Exception:
            logger.exception("orch task {}: status failed not written", task_id)
            await self._release_unsettled(task_id, session_id, error)
            return
        try:
            await self.bus.publish(session_id, "task_failed", {"error": error}, task_id=task_id)
        except Exception:
            logger.exception("orch task {}: task_failed not published", task_id)

    async def _release_unsettled(self, task_id: int, session_id: str, error: str) -> None:
        self._unsettled.add(task_id)
        if self._session_task.get(session_id) == task_id:
            self._session_task.pop(session_id, None)
        try:
            await self.bus.publish(session_id, "task_failed", {"error": error}, task_id=task_id)
        except Exception:
            logger.exception("orch task {}: task_failed not published", task_id)
        try:
            await self._announce_result(task_id, session_id, TaskStatus.failed, error)
        except Exception:
            logger.exception("orch result message failed for task {}", task_id)
        self._spawn(self._mark_failed_later(task_id, session_id, error))
        try:
            await self._start_queued(session_id, task_id)
        except Exception:
            logger.exception("orch queue after task {} not started", task_id)

    async def _mark_failed_later(self, task_id: int, session_id: str, error: str) -> None:
        for _ in range(max(1, int(self.failed_mark_retries))):
            await asyncio.sleep(self.failed_mark_delay_s)
            try:
                current = self._status(task_id)
                if current is None or current in TERMINAL_STATUSES:
                    self._unsettled.discard(task_id)
                    return
                await write_on_loop(
                    self._write_status,
                    task_id,
                    TaskStatus.failed,
                    error,
                    budget_s=self.status_write_budget_s,
                    what=f"task {task_id} -> failed (later)",
                )
            except InvalidTransition:
                self._unsettled.discard(task_id)
                return
            except Exception:
                logger.warning("orch task {}: status failed still not written", task_id)
                continue
            self._unsettled.discard(task_id)
            self.history.append((task_id, TaskStatus.failed.value))
            try:
                await self.bus.publish(
                    session_id,
                    "agent_status",
                    {
                        "status": TaskStatus.failed.value,
                        "agent": "supervisor",
                        "task_id": task_id,
                        "resume_allowed": False,
                    },
                    task_id=task_id,
                )
            except Exception:
                logger.exception("orch task {}: agent_status not published", task_id)
            return
        logger.error("orch task {}: gave up writing status failed", task_id)

    def _watch_run(self, task_id: int, session_id: str, job: asyncio.Task[Any]) -> asyncio.Task[Any]:
        def finished(done: asyncio.Task[Any]) -> None:
            if done.cancelled():
                return
            exc = done.exception()
            if not isinstance(exc, Exception):
                return
            logger.opt(exception=exc).error("orch task {} crashed", task_id)
            if task_id in self._paid_open or task_id in self._shield_orphaned:
                return
            try:
                self._spawn(self._settle_failure(task_id, session_id, exc, keep_held=True))
            except RuntimeError:
                return

        job.add_done_callback(finished)
        return job

    async def _start_queued(self, session_id: str, finished_id: int) -> None:
        """Дренаж только после терминального статуса.

        Память очереди заполняет ``submit_task`` и ``recover_after_restart``
        (строки ``queued`` из БД). ``paused`` и ``waiting_approval`` очередь
        не отпускают: ``resume`` и ``grant_approval`` продолжают ту же задачу,
        вторая рядом не стартует.
        """
        if self._session_task.get(session_id) not in {None, finished_id}:
            return
        if session_id in self._kill:
            return
        pending = self._queued.get(session_id) or []
        if not pending:
            return
        task_id, goal = pending.pop(0)
        self._cancel.discard(session_id)
        self._pause.discard(session_id)
        self._steer.pop(session_id, None)
        self._approval.pop(session_id, None)
        self._session_task[session_id] = task_id
        self._change_ids[task_id] = []
        self._context[task_id] = ""
        self._paid_done[task_id] = set()
        self._runs[task_id] = self._watch_run(
            task_id,
            session_id,
            asyncio.create_task(
                self._drive(task_id, session_id, goal),
                name=f"orch-task-{task_id}",
            ),
        )

    def _status(self, task_id: int) -> TaskStatus | None:
        row = self.tasks.get(task_id)
        if row is None:
            return None
        return TaskStatus(str(row["status"]))

    def _spend(self, task_id: int) -> SpendSnapshot:
        row = self.tasks.get(task_id) or {}
        return SpendSnapshot(
            internal=float(row.get("spend_internal") or 0),
            actions=float(row.get("spend_actions") or 0),
        )


_FIELD_RU = {"step_code": "шаг конвейера", "project_id": "номер проекта", "node_key": "нода"}
_SCHEMA_FIELD = re.compile(r"^\$\.([A-Za-z0-9_]+)")


def _sanity_feedback(plan: list[PlanStep], result: ValidatorResult) -> tuple[str, str]:
    details = result.details if isinstance(result.details, dict) else {}
    errors = [item for item in details.get("errors") or [] if isinstance(item, dict)]
    machine: list[str] = []
    human: list[str] = []
    for item in errors[:6]:
        index = item.get("index")
        action = str(item.get("action") or "")
        reason = str(item.get("reason") or "")
        message = str(item.get("message") or "")
        machine.append(
            f"шаг {int(index) + 1 if isinstance(index, int) else '?'} {action}: {message or reason}"
        )
        title = task_summary.step_title(action)
        field = str(item.get("field") or "")
        found = _SCHEMA_FIELD.match(message)
        if found:
            field = found.group(1)
        params = plan[index].params if isinstance(index, int) and 0 <= index < len(plan) else {}
        label = _FIELD_RU.get(field, f"параметр {field}" if field else "параметр")
        if reason == "missing_param":
            human.append(f"«{title}»: не хватает значения ({label}).")
        elif reason in {"schema", "bad_type"}:
            value = params.get(field) if field else None
            shown = f" «{value}»" if value not in (None, "") else ""
            human.append(f"«{title}»: недопустимое значение{shown} ({label}).")
        elif reason == "not_in_whitelist":
            human.append(f"«{action}»: такого действия нет среди разрешённых.")
        elif reason == "missing_verify_code_state":
            human.append(f"«{title}»: перед запуском нет проверки состояния проекта.")
        else:
            human.append(f"«{title}»: шаг не прошёл проверку.")
    planner = (
        "Проверка плана отклонила шаги: " + "; ".join(machine or ["причина не указана"]) + ". "
        "Исправь план: только id из каталога, значения параметров только из поля v каталога."
    )
    return planner, "Что не так в плане: " + " ".join(human or ["шаги не прошли проверку."])


#: «Подготовь правку, но не применяй»: применение тогда не предлагаем.
_NO_APPLY_RE = re.compile(r"не\s+примен|без\s+применени|только\s+(?:подготов|предлож)", re.IGNORECASE)


def _follow_up_steps(
    plan: list[PlanStep], started: list[tuple[PlanStep, int]], results: list[Any], goal: str
) -> list[PlanStep]:
    """``change.apply`` для каждой только что подготовленной правки промта, которой нет в плане."""
    if _NO_APPLY_RE.search(goal or ""):
        return []
    planned = {str(step.params.get("proposal_id") or "") for step in plan if step.action == "change.apply"}
    out: list[PlanStep] = []
    for (step, _step_id), result in zip(started, results, strict=False):
        if step.action != "prompt.edit_text" or envelope_status(result) != "ok":
            continue
        body = envelope_body(result)
        apply = body.get("apply_step") if isinstance(body, dict) else None
        params = apply.get("params") if isinstance(apply, dict) else None
        if not isinstance(params, dict) or apply.get("action_id") != "change.apply":
            continue
        proposal = str(params.get("proposal_id") or "")
        if not proposal or proposal in planned or not params.get("expected_digest"):
            continue
        planned.add(proposal)
        out.append(
            PlanStep(
                action="change.apply",
                params={"proposal_id": proposal, "expected_digest": str(params["expected_digest"])},
                expected="Правка промта применена, прежний текст сохранён версией.",
                paid=False,
                mutating=True,
                risk="high",
            )
        )
    return out


def _proposes_prompt_text(plan: list[PlanStep]) -> bool:
    return any(
        step.action == "change.propose"
        and (step.params.get("kind") == "prompt_text" or step.params.get("tier") == "T2")
        for step in plan
    )


def _goal_words(text: str) -> list[str]:
    plain = str(text or "").lower().replace("ё", "е")
    return re.sub(r"[^\w\s]", " ", plain).split()


def _same_goal(left: list[str], right: list[str]) -> bool:
    if not left or not right:
        return False
    if left == right:
        return True
    a, b = set(left), set(right)
    if min(len(a), len(b)) < 4:
        return False
    return len(a & b) / len(a | b) >= 0.8


def _appr_key(task_id: int | None) -> int:
    if task_id is None:
        return 0
    return int(task_id)


def _server_change_id(key: int, value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    match = _APPR_ID.fullmatch(value)
    if match is None or int(match.group(1)) != key:
        return None
    return value


def _split_appr_context(context: str) -> tuple[str, dict[str, Any] | None]:
    if _APPR_MARK not in context:
        return context, None
    visible, _, raw = context.rpartition(_APPR_MARK)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return context, None
    if not isinstance(data, dict):
        return context, None
    return visible, data


def _plan_fingerprint(plan: list[PlanStep]) -> str:
    payload = [step.model_dump() for step in plan]
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _pack_approved(
    visible: str,
    fingerprint: str | None,
    block_reason: str | None = None,
) -> str:
    """Отпечаток approve и причина блока лежат в поле ``context`` чекпоинта."""
    base = _strip_unknown((visible or "").split(_APPROVED_MARK, 1)[0]).split(_BLOCK_MARK, 1)[0]
    packed = base
    if fingerprint:
        packed += f"{_APPROVED_MARK}{fingerprint}"
    if block_reason in _PERSISTED_BLOCKS:
        packed += f"{_BLOCK_MARK}{block_reason}"
    return packed


def _unpack_approved(raw: str) -> tuple[str, str | None]:
    text = raw or ""
    if _APPROVED_MARK not in text:
        return _strip_unknown(text).split(_BLOCK_MARK, 1)[0], None
    visible, mark = text.split(_APPROVED_MARK, 1)
    mark = mark.split(_UNKNOWN_MARK, 1)[0].split(_BLOCK_MARK, 1)[0].strip()
    return _strip_unknown(visible).split(_BLOCK_MARK, 1)[0], mark or None


def _strip_unknown(text: str) -> str:
    return (text or "").split(_UNKNOWN_MARK, 1)[0]


def _pack_unknown(visible: str, info: dict[str, Any] | None) -> str:
    base = _strip_unknown(visible)
    if not info:
        return base
    payload = json.dumps(info, ensure_ascii=False, sort_keys=True)
    return f"{base}{_UNKNOWN_MARK}{payload}"


def _unpack_unknown(raw: str) -> dict[str, Any] | None:
    if _UNKNOWN_MARK not in (raw or ""):
        return None
    chunk = raw.split(_UNKNOWN_MARK, 1)[1]
    for mark in (_APPR_MARK, _APPROVED_MARK, _BLOCK_MARK):
        chunk = chunk.split(mark, 1)[0]
    try:
        data = json.loads(chunk)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or "position" not in data:
        return None
    return data


def _unpack_block(raw: str) -> str | None:
    if _BLOCK_MARK not in (raw or ""):
        return None
    reason = raw.split(_BLOCK_MARK, 1)[1]
    for mark in (_APPR_MARK, _UNKNOWN_MARK, _APPROVED_MARK, "\n"):
        reason = reason.split(mark, 1)[0]
    reason = reason.strip()
    return reason or None


def _step_requires_project(step: PlanStep, actions: Any) -> bool:
    """Не read-only по реестру, либо план ужесточил шаг (paid / mutating / риск)."""
    if step_is_readonly(step, actions):
        return False
    probe = getattr(actions, "project_required", None)
    if not callable(probe):
        return True
    try:
        needed = probe(step.action)
    except Exception:
        return True
    return needed is not False


def _plan_requires_project(plan: list[PlanStep], actions: Any) -> bool:
    return any(_step_requires_project(step, actions) for step in plan)


_CHAIN_ACTIONS = frozenset({"hitl.approve", "step.run_step"})


_RUN_ON_RE = re.compile(r"(пусть|дальше|потом)\W+(\w+\W+){0,3}(сам[аи]?|самостоятельно)\b")


def _chain_from_queue(plan: list[PlanStep], goal: str = "") -> list[PlanStep]:
    """Шаги запуска/подтверждения идут цепочкой, если так решил человек или сам план.

    Человек: «дальше пусть идут сами». План: queue.enqueue_many(enable_auto_mode) для проекта.
    Планировщик иногда пишет allow_auto_chain=false вопреки этому, и шаг отказывается
    («включено автопродвижение»). Карточка показывает итоговое значение.
    """
    said = bool(_RUN_ON_RE.search(" ".join((goal or "").lower().replace("ё", "е").split())))
    chained: set[int] = set()
    for step in plan:
        if step.action == "queue.enqueue_many" and step.params.get("enable_auto_mode"):
            chained.update(p for p in step.params.get("project_ids") or [] if isinstance(p, int))
    if not chained and not said:
        return plan
    return [
        step.model_copy(update={"params": {**step.params, "allow_auto_chain": True}})
        if step.action in _CHAIN_ACTIONS and (said or _step_project(step) in chained)
        else step
        for step in plan
    ]


_SETTINGS_ACTIONS = frozenset({"project.patch_settings", "project.patch_settings_costly"})
# «Поставь модель по умолчанию / дефолтную / стандартную», «сбрось модель».
_ASKS_DEFAULT = re.compile(r"умолчан|дефолт|default|стандартн|сброс|сбрось")


def _ids_ru(ids: list[int]) -> str:
    return ", ".join(f"#{pid}" for pid in ids)


def _projects_word(count: int) -> str:
    if count % 10 == 1 and count % 100 != 11:
        return "проект"
    if count % 10 in (2, 3, 4) and count % 100 not in (12, 13, 14):
        return "проекта"
    return "проектов"


def _same_of(skipped: list[tuple[int | None, str]]) -> str:
    """Общая для всех пропущенных фраза «модель картинок уже GPT Image 2», иначе пусто."""
    texts = [text for _, text in skipped]
    return texts[0] if texts and texts[0] and all(text == texts[0] for text in texts) else ""


_LAUNCH_SKIPPED_CRITIC = (
    "Проверка кода до карточки (не ошибка плана): часть названных человеком проектов намеренно "
    "убрана из плана, потому что не проходит жёсткое условие запуска. Человек увидит это первой "
    "строкой карточки: «{note}». Не требуй вернуть эти проекты и не считай их отсутствие "
    "причиной для revise или escalate; проверяй только оставшиеся шаги."
)


def _blocked_launch_text(
    head: tuple[str, str], dropped: list[tuple[int, str]], kept: list[int], goes: tuple[str, str]
) -> str:
    """«Не поставлены в очередь: #10, #12 — кадры без данных сцены, …» и как запустить их потом.

    ``head`` и ``goes`` — формы (один проект, несколько). Проекты с одной причиной идут одной
    группой; оставшийся один проект назван номером, а не «остальной проект».
    """
    first: dict[int, str] = {}
    for pid, reason in dropped:
        first.setdefault(pid, reason)
    groups: dict[str, list[int]] = {}
    for pid, reason in first.items():
        groups.setdefault(reason.rstrip("."), []).append(pid)
    why = ". ".join(f"{_ids_ru(pids)} — {reason}" for reason, pids in groups.items())
    one = len(first) == 1
    ids = ", ".join(str(pid) for pid in first)
    kept = list(dict.fromkeys(kept))
    if len(kept) == 1:
        rest = f"Проект #{kept[0]} {goes[0]}."
    else:
        rest = f"Остальные {len(kept)} {_projects_word(len(kept))} {goes[1]}."
    later = (
        f"Когда причина будет устранена, {'проект' if one else 'эти проекты'} можно запустить "
        f"отдельно: «запусти {'проект' if one else 'проекты'} {ids}»."
    )
    return f"{head[0] if one else head[1]}: {why}. {rest} {later}"


def _skipped_text(skipped: list[tuple[int | None, str]]) -> str:
    ids = [pid for pid, _ in skipped if pid is not None]
    same = _same_of(skipped)
    tail = f": {same}" if same else ": там уже так, как просите"
    return (
        f"Пропущено {len(skipped)} {_projects_word(len(skipped))} ({_ids_ru(ids)}), менять там нечего{tail}."
    )


def _nothing_text(skipped: list[tuple[int | None, str]]) -> str:
    ids = [pid for pid, _ in skipped if pid is not None]
    same = _same_of(skipped)
    if len(skipped) == 1:
        where = f"В проекте #{ids[0]}" if ids else "В проекте"
        return f"{where} {same or 'всё уже так, как просите'}, менять нечего."
    if same:
        return f"Во всех {len(skipped)} проектах ({_ids_ru(ids)}) {same}, менять нечего."
    return f"В проектах {_ids_ru(ids)} всё уже так, как просите, менять нечего."


def _queue_limit() -> int | None:
    """Сколько проектов очереди сейчас идут одновременно; None — не прочитать."""
    try:
        from app.services.gen_queue import worker_max_parallel

        value = int(worker_max_parallel())
    except Exception:
        return None
    return value if value > 0 else None


def _step_project(step: PlanStep) -> int | None:
    pid = step.params.get("project_id")
    return pid if isinstance(pid, int) and not isinstance(pid, bool) and pid > 0 else None


def _plan_missing_project(plan: list[PlanStep], actions: Any) -> bool:
    """Есть шаг, которому нужен проект, а он его не назвал."""
    return any(_step_requires_project(step, actions) and _step_project(step) is None for step in plan)


def _plan_single_project(plan: list[PlanStep], actions: Any) -> int | None:
    """Один project_id во всех изменяющих шагах — задача из «Вся фабрика» берёт его."""
    ids: set[Any] = set()
    for step in plan:
        if _step_requires_project(step, actions):
            ids.add(step.params.get("project_id"))
    if len(ids) != 1:
        return None
    pid = ids.pop()
    return pid if isinstance(pid, int) and not isinstance(pid, bool) and pid > 0 else None


def _public_params(params: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in params.items() if not str(key).startswith("_")}


def _readonly_batch(plan: list[PlanStep], index: int, actions: Any) -> list[PlanStep]:
    step = plan[index]
    group = step.parallel_group
    if not group or not _can_run_parallel(step, actions):
        return [step]
    batch = [step]
    cursor = index + 1
    while cursor < len(plan):
        nxt = plan[cursor]
        if nxt.parallel_group != group or not _can_run_parallel(nxt, actions):
            break
        batch.append(nxt)
        cursor += 1
    return batch


def _can_run_parallel(step: PlanStep, actions: Any) -> bool:
    return step_is_readonly(step, actions)


# Платные запуски, которые можно вести одновременно: каждый в своём проекте.
PARALLEL_RUN_ACTIONS: frozenset[str] = frozenset({"step.run_step", "node.run_step"})


def _parallel_runs_ok(batch: list[PlanStep], actions: Any) -> bool:
    """Группа из 2+ платных запусков списка, у каждого свой положительный project_id."""
    if len(batch) < 2:
        return False
    seen: set[int] = set()
    for step in batch:
        if step.action not in PARALLEL_RUN_ACTIONS or not step.paid:
            return False
        if step_is_readonly(step, actions) or frame_soft_delete_state(step.action, actions) is not None:
            return False
        project = _step_project(step)
        if project is None or project in seen:
            return False
        seen.add(project)
    return True


def group_fingerprint(plan: list[PlanStep], positions: list[int]) -> str:
    """Отпечаток шагов групповой карточки: позиция, действие, публичные params, группа."""
    payload = [
        [at, paid_step_identity(plan[at].action, plan[at].params), plan[at].parallel_group]
        for at in positions
    ]
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def group_step_change_id(card: str, position: int) -> str:
    """Id одобрения шага групповой карточки: мост привязывает к нему план ядра этого шага."""
    return f"{card}-p{int(position)}"


def _card_reason(risk: Risk, gate: str | None) -> str:
    if risk in APPROVAL_RISKS:
        return "risk"
    return "paid" if gate == "paid" else "destructive"


def _long_core_step(action: str) -> bool:
    """Долгий шаг с подтверждением ядра. Подтверждение живёт ограниченное время:
    цепочка таких шагов по очереди пережила бы его, поэтому заранее их не одобряем."""
    spec = approval_steps._spec(action)
    return (
        spec is not None
        and bool(getattr(spec, "needs_approval", False))
        and getattr(spec, "poll", None) is not None
    )


def _meta_flag(meta: Any, key: str) -> Any:
    if isinstance(meta, dict):
        return meta.get(key)
    return getattr(meta, key, None)


def _free_read_step(step: PlanStep, actions: Any) -> bool:
    kind, meta = lookup_action_metadata(step.action, actions)
    if kind != "value" or meta is None:
        return False
    if (
        _meta_flag(meta, "read_only") is not True
        or _meta_flag(meta, "paid")
        or _meta_flag(meta, "destructive")
    ):
        return False
    if coerce_risk(_meta_flag(meta, "risk") or "high") is not Risk.none:
        return False
    if step.paid or step.mutating or not step_is_readonly(step, actions):
        return False
    if escalation_for(step.action, step.params) is not None or metadata_call_gate(step.action, actions):
        return False
    return effective_risk(actions, step) is Risk.none


def _free_read_plan(plan: list[PlanStep], actions: Any) -> bool:
    return bool(plan) and all(_free_read_step(step, actions) for step in plan)


def _monotonic() -> float:
    return time.monotonic()


_REASON_PROJECT = re.compile(r"проект\w*\s*(?:№|#)?\s*(\d+)", re.IGNORECASE)
_REASON_NODE_KEY = re.compile(r"(?<![\w.])(n_[A-Za-z0-9_]+)")
_NODE_META_TIMEOUT_S = 5.0


async def _load_node_meta(project_id: int) -> Any:
    from app.orchestrator.agents.project_nodes import load_meta

    return await load_meta(int(project_id))


def _node_model_kind(meta: Any, reason: str, goal: str) -> str | None:
    """Вид модели ноды по её типу на холсте: ключ из причины, иначе одна нода из просьбы."""
    from app.orchestrator.agents.project_nodes import match_nodes, roster
    from app.services import node_edit

    if not isinstance(meta, dict):
        return None
    keys = list(dict.fromkeys(_REASON_NODE_KEY.findall(reason or "")))
    if not keys:
        keys = match_nodes(roster(meta), goal or "")
    kinds = set()
    for key in keys:
        node = node_edit.find_node(meta, key)
        if node is None:
            continue
        kind = node_edit.model_kind_for_type(node_edit.node_type(node))
        if kind:
            kinds.add(kind)
    return kinds.pop() if len(kinds) == 1 else None


# Продолжение проекта: условия запуска (проект есть, на паузе или шаг готов, монтаж не идёт)
# перечитывают живое состояние в момент исполнения, карточка подтверждения обязательна.
RESUME_ACTIONS: frozenset[str] = frozenset({"project.continue_pipeline", "project.resume"})


def _step_project(step: PlanStep) -> Any:
    return (step.params or {}).get("project_id")


def _critic_checks_clear(plan: list[PlanStep]) -> bool:
    """Проверки критика без модели (удаления, потолок, прогноз трат выше лимита) молчат.

    Одобрение плана без круга критика их не пропускает: если хоть одна сработала, план идёт
    к критику, и человек увидит «прогноз трат выше лимита», а не простую карточку.
    """
    from app.orchestrator.agents.critic import deterministic_verdict

    try:
        return deterministic_verdict(plan) is None
    except Exception:  # noqa: BLE001 — неизвестный прогноз: судит критик
        logger.exception("orch critic checks failed before pre-approval")
        return False


def _same_project(a: Any, b: Any) -> bool:
    return a not in (None, "") and b not in (None, "") and str(a).strip() == str(b).strip()


def _resume_after_read_plan(plan: list[PlanStep], actions: Any) -> bool:
    """План «project.status → одно продолжение проекта» того же проекта: критику проверять нечего.

    Чтение состояния этого проекта (project.status) уже стоит перед продолжением, а условия
    запуска продолжения ещё раз проверяют живое состояние при исполнении. Без чтения, без
    карточки (риск ниже порога), с лишними параметрами (другие проекты, тенант, «до отчёта»)
    или с чтением чужого проекта под другим ключом — критик остаётся.
    """
    resumes = [step for step in plan if step.action in RESUME_ACTIONS]
    if len(resumes) != 1 or plan[-1] is not resumes[0]:
        return False
    target = _step_project(resumes[0])
    if set(resumes[0].params or {}) != {"project_id"} or target in (None, ""):
        return False
    if effective_risk(actions, resumes[0]) not in APPROVAL_RISKS:
        return False
    for step in plan[:-1]:
        if step.action != "project.status" or not _free_read_step(step, actions):
            return False
        if set(step.params or {}) != {"project_id"} or not _same_project(_step_project(step), target):
            return False
    return len(plan) > 1


# Шаги постановки в очередь: исполняются только после «Подтвердить» на карточке.
QUEUE_LAUNCH_ACTIONS: frozenset[str] = frozenset({"queue.enqueue_many", "queue.set_parallel"})


def _queue_launch_plan(plan: list[PlanStep], actions: Any) -> bool:
    """План только из очереди генерации и бесплатных чтений; каждый шаг очереди идёт карточкой.

    Такой план критику проверять нечего: проекты и режим человек видит на карточке и
    подтверждает сам, а платные шаги потом запускает воркер по своей проверке.
    """
    if not any(step.action in QUEUE_LAUNCH_ACTIONS for step in plan):
        return False
    for step in plan:
        if step.action in QUEUE_LAUNCH_ACTIONS:
            if step.paid or effective_risk(actions, step) not in APPROVAL_RISKS:
                return False
        elif not _free_read_step(step, actions):
            return False
    return True
