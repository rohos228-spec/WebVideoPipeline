"""Чат оркестратора: приём сообщения, SSE-поток и решение по approval.

POST ``/api/orchestrator/chat/{session}/message``
GET  ``/api/orchestrator/chat/{session}/status``
GET  ``/api/orchestrator/chat/{session}/stream``  (Last-Event-ID)
POST ``/api/orchestrator/chat/{session_id}/approve``
POST ``/api/orchestrator/chat/{session}/reject``   (отказ по карточке: задача отменена, план отозван)
POST ``/api/orchestrator/chat/{session}/kill``     (стоп задачи чата и её проекта)
GET  ``/api/orchestrator/chat/{session}/partial-changes``

Чат открыт каждому клиенту студии. Имя сессии из пути — внешнее; внутрь
движка уходит имя с арендатором (``orch_tenancy.scoped_session``), так что
одинаково названные чаты двух клиентов — разные сессии со своей очередью,
нумерацией событий и карточками одобрения. Строки задач, событий и планов
закрыты RLS: чужая задача или план для клиента не существуют — 404.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator
from sse_starlette.sse import EventSourceResponse

from app.orchestrator.agents.runtime import get_runtime
from app.orchestrator.agents.supervisor import (
    ApprovalGrantError,
    InvalidTransition,
    ProjectBindError,
    Supervisor,
)
from app.orchestrator.contracts import MessageMode, OrchEvent
from app.services import orch_bridge
from app.services.action_layer import approval as al_approval
from app.services.action_layer import planner as al_planner
from app.services.orch_tenancy import public_session, scoped_session
from app.services.plain_ru import plain_ru
from app.web.engine_access import require_engine_member
from app.web.member_money import MEMBER_COST_TEXT, member_event_payload, member_safe  # noqa: F401

router = APIRouter(
    prefix="/orchestrator/chat",
    tags=["orchestrator-chat"],
    # Доступ по личности новой линии (IdentityMiddleware): любой клиент студии.
    dependencies=[Depends(require_engine_member)],
)

_SESSION_RE = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
# Как часто перепроверять дисконнект, пока в очереди тишина.
_STREAM_POLL_SEC = 1.0


class MessageIn(BaseModel):
    text: str = Field(min_length=1, max_length=16000)
    mode: MessageMode | None = None
    project_id: int | None = None
    kill_switch: bool = False

    @model_validator(mode="before")
    @classmethod
    def _drop_client_change_id(cls, data: Any) -> Any:
        """change_id одобрения назначает только сервер. Поле клиента отбрасывается."""
        if isinstance(data, dict) and "change_id" in data:
            data = dict(data)
            data.pop("change_id", None)
        return data


class ProjectIn(BaseModel):
    """Поздний project_id. Строка, float, bool, ``0`` и отрицательные — 422."""

    model_config = ConfigDict(extra="forbid")

    project_id: StrictInt = Field(ge=1)
    task_id: StrictInt | None = Field(default=None, ge=1)


class ApproveIn(BaseModel):
    """Тело ``POST .../approve``. Лишние поля запрещены. ``decision=null`` — старый grant."""

    model_config = ConfigDict(extra="forbid")

    change_id: str | None = None
    decision: Literal["approve", "retry_step", "deny"] | None = None
    retry_grant: bool = False


class RejectIn(BaseModel):
    """Тело ``POST .../reject``: какую карточку отклонить."""

    model_config = ConfigDict(extra="forbid")

    change_id: str | None = Field(default=None, max_length=200)
    reason: str | None = Field(default=None, max_length=500)


class KillIn(BaseModel):
    """Тело ``POST .../kill``. Проект — тот, что открыт в чате (иначе проект задачи)."""

    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(default=None, max_length=500)
    project_id: StrictInt | None = Field(default=None, ge=1)


KILL_DETAIL = (
    "Задача этого чата остановлена: неплатные шаги отменены, новые действия задачи не "
    "запускаются. Платный шаг, который уже идёт, остановится на границе. Аварийный стоп "
    "всей студии этой кнопкой не включается."
)


def _check_session(session: str) -> str:
    """Проверить внешнее имя и вернуть внутреннее — с арендатором запроса."""
    if not _SESSION_RE.fullmatch(session):
        raise HTTPException(status_code=400, detail="bad session")
    return scoped_session(session)


async def _assert_project_visible(project_id: int | None) -> None:
    """Проект из тела или запроса виден текущему арендатору, иначе 404.

    В режиме владельца проверки нет (прежнее поведение). Под арендатором
    чужой проект не существует: без этой проверки поток уведомлений чата
    подписался бы на чужой проект по одному его номеру.
    """
    from app.services.tenant import current_tenant

    if project_id is None or current_tenant() is None:
        return
    from app.db import SessionLocal
    from app.models import Project

    async with SessionLocal() as session:
        found = await session.get(Project, int(project_id))
    if found is None:
        raise HTTPException(status_code=404, detail="Проект не найден")


def _public(payload: Any) -> Any:
    """Ответ движка с внешним именем сессии вместо внутреннего."""
    if isinstance(payload, dict) and isinstance(payload.get("session"), str):
        return {**payload, "session": public_session(payload["session"])}
    return payload


def remember_seq(cursor: int, seq: int) -> tuple[int, bool]:
    """Отдать seq один раз. Курсор — последнее число, не растущее множество."""
    if seq <= cursor:
        return cursor, False
    return seq, True


def _for_viewer(payload: Any) -> Any:
    from app.services.action_layer.plan import member_view

    return member_safe(payload, chat=True) if member_view() else payload


def _sse(event: OrchEvent) -> dict[str, str]:
    """Событие в SSE. Клиент видит своё внешнее имя сессии, без арендатора; участник — без $."""
    if event.session_id:
        event = event.model_copy(update={"session_id": public_session(event.session_id)})
    from app.services.action_layer.plan import member_view

    if member_view():
        event = event.model_copy(update={"payload": member_event_payload(event.type, event.payload)})
    return {
        "event": event.type,
        "id": str(event.seq),
        "data": event.model_dump_json(),
    }


@router.post("/{session}/message")
async def post_message(session: str, body: MessageIn, request: Request) -> dict[str, object]:
    """Принять реплику. Ack уходит в шину до классификации, ответ HTTP её не ждёт."""
    internal = _check_session(session)
    await _assert_project_visible(body.project_id)
    runtime = get_runtime()
    ack_seq = await runtime.queue.accept(
        internal,
        body.text,
        body.mode,
        project_id=body.project_id,
        kill_switch=body.kill_switch,
        app=request.app,
    )
    return {"ok": True, "session": session, "ack_seq": ack_seq}


def _approval_result(
    supervisor: Supervisor,
    session_id: str,
    body: ApproveIn,
    result: dict[str, Any] | None,
) -> dict[str, object]:
    """Поля, которые WS4 читает из 200. Пустой grant не выдумывает executed."""
    payload = result if isinstance(result, dict) else {}
    task_id = supervisor._session_task.get(session_id)
    key = int(task_id) if task_id is not None else None
    if "retry_armed" in payload:
        armed = bool(payload["retry_armed"])
    else:
        armed = key is not None and key in supervisor._retry_armed
    change_id = body.change_id or payload.get("change_id")
    if not change_id and key is not None:
        change_id = supervisor._appr_open.get(key)
        if not change_id:
            for candidate, owner in supervisor._appr_owner.items():
                if owner == (key, session_id) and candidate in supervisor._appr_decided:
                    change_id = candidate
    decision = payload.get("decision", body.decision)
    forecast = payload.get("forecast_usd")
    return {
        "executed": bool(payload.get("executed", False)),
        "reason": payload.get("reason"),
        "forecast_usd": forecast,
        "retry_armed": armed,
        "change_id": change_id,
        "decision": decision,
    }


@router.post("/{session}/project")
async def post_project(session: str, body: ProjectIn) -> dict[str, object]:
    """Снять ``missing_project_id`` целым id проекта и продолжить задачу."""
    internal = _check_session(session)
    await _assert_project_visible(body.project_id)
    runtime = get_runtime()
    try:
        result = await runtime.supervisor.bind_project(internal, body.project_id, task_id=body.task_id)
        return _public(result)
    except ProjectBindError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


def operator_required_response() -> JSONResponse:
    message = "Подтвердить может только человек из Studio: обновите страницу и повторите."
    return JSONResponse(
        status_code=403,
        content={"detail": {"code": "operator_token_required", "message": message}, "message": message},
    )


async def human_decision(
    request: Request,
    *,
    session_id: str,
    change_id: str | None,
    decision: str | None,
    retry_grant: bool,
) -> tuple[Any, str | None]:
    runtime = get_runtime()
    if not (retry_grant or decision in (None, "approve")):
        result = await runtime.supervisor.grant_approval(
            session_id, decision, retry_grant=retry_grant, change_id=change_id
        )
        return result, None
    operator = orch_bridge.human_operator(dict(request.scope))
    if operator is None:
        raise orch_bridge.DecisionError(403, "operator_token_required")
    return await orch_bridge.approve(
        runtime,
        request.app,
        session_id=session_id,
        change_id=change_id,
        decision=decision,
        retry_grant=retry_grant,
        operator=operator,
    )


def decision_failure(exc: BaseException, *, missing: str = "not found") -> HTTPException | JSONResponse:
    if isinstance(exc, orch_bridge.DecisionError):
        if exc.status == 403:
            return operator_required_response()
        return HTTPException(status_code=exc.status, detail=exc.detail)
    if isinstance(exc, ApprovalGrantError):
        return HTTPException(status_code=exc.status_code, detail=exc.detail)
    if isinstance(exc, InvalidTransition):
        return HTTPException(status_code=409, detail="invalid transition")
    if isinstance(exc, al_approval.ApprovalError):
        if exc.code in {"operator_required", "operator_token_required"}:
            return operator_required_response()
        return HTTPException(status_code=409, detail=plain_ru(str(exc)))
    if isinstance(exc, al_planner.StalePlan):
        return HTTPException(status_code=409, detail=plain_ru(str(exc)))
    return HTTPException(status_code=404, detail=missing)


DECISION_ERRORS = (
    orch_bridge.DecisionError,
    ApprovalGrantError,
    InvalidTransition,
    al_approval.ApprovalError,
    al_planner.StalePlan,
    LookupError,
)


@router.post("/{session_id}/approve")
async def post_approve(session_id: str, body: ApproveIn, request: Request):
    """Решение человека. ``decision=null`` идёт прежним ``grant_approval``."""
    internal = _check_session(session_id)
    runtime = get_runtime()
    try:
        result, _plan_id = await human_decision(
            request,
            session_id=internal,
            change_id=body.change_id,
            decision=body.decision,
            retry_grant=body.retry_grant,
        )
    except DECISION_ERRORS as exc:
        failure = decision_failure(exc)
        if isinstance(failure, JSONResponse):
            return failure
        raise failure from None
    return _for_viewer(_approval_result(runtime.supervisor, internal, body, result))


@router.post("/{session}/reject")
async def post_reject(session: str, body: RejectIn, request: Request):
    """Отказ человека по карточке: задача отменяется без исполнения, план слоя действий отозван.

    Сессия — внутренняя, с арендатором: чужая карточка здесь не найдётся (404).
    """
    internal = _check_session(session)
    runtime = get_runtime()
    try:
        result = await runtime.supervisor.reject_approval(internal, change_id=body.change_id)
    except DECISION_ERRORS as exc:
        failure = decision_failure(exc, missing="approval not found")
        if isinstance(failure, JSONResponse):
            return failure
        raise failure from None
    # Какая карточка закрыта, решает сервер (supervisor), а не тело запроса: на пути
    # протухшей карточки reject_approval не сверяет change_id клиента.
    decided = result.get("change_id") if isinstance(result, dict) else None
    plan_id = await orch_bridge.bound_plan_any(decided) if decided else None
    if plan_id:
        await orch_bridge.revoke(plan_id, decided, orch_bridge.actor_of(dict(request.scope)))
    return {
        **_public(result),
        "ok": True,
        "approval_id": decided or body.change_id,
        "decision": "reject",
        "matched": True,
    }


@router.post("/{session}/kill")
async def post_kill(session: str, body: KillIn, request: Request) -> dict[str, object]:
    """Стоп задачи этого чата и её проекта (проект — только видимый арендатору)."""
    internal = _check_session(session)
    await _assert_project_visible(body.project_id)
    runtime = get_runtime()
    task_id = runtime.supervisor._session_task.get(internal)
    project_id = body.project_id or orch_bridge.session_project(runtime, internal)
    job = runtime.supervisor.request_kill(internal)
    if job is not None:
        await job
    detail = KILL_DETAIL
    project: dict | None = None
    if project_id is not None:
        project = await orch_bridge.stop_session_project(request.app, project_id, task_id)
        if not project["ok"]:
            detail += f" Проект #{project_id} остановить не удалось: {project['message'] or 'нет ответа'}"
        elif project["stopped"]:
            detail += f" Проект #{project_id} остановлен."
        else:
            detail += f" В проекте #{project_id} ничего не шло."
    return {
        "ok": True,
        "status": "cancelled",
        "stop_key_set": True,
        "scope": "session",
        "session_id": session,
        "task_id": task_id,
        "project_id": project_id,
        "project_stopped": bool(project and project["stopped"]),
        "detail": detail,
    }


@router.post("/{session}/resume")
async def post_resume(session: str) -> dict[str, object]:
    """Продолжить с чекпоинта.

    ``cancelled`` → ``executing`` — 409. Нет задачи или чекпоинта — 404.
    """
    internal = _check_session(session)
    runtime = get_runtime()
    task_id = runtime.supervisor.active_task(internal)
    if task_id is None:
        task_id = runtime.supervisor._session_task.get(internal)
    if task_id is None:
        raise HTTPException(status_code=404, detail="not found")
    try:
        await runtime.supervisor.resume(task_id)
    except InvalidTransition:
        raise HTTPException(status_code=409, detail="invalid transition") from None
    except LookupError:
        raise HTTPException(status_code=404, detail="not found") from None
    status = runtime.supervisor._status(task_id)
    return {
        "ok": True,
        "session": session,
        "task_id": task_id,
        "status": status.value if status is not None else "",
    }


@router.get("/{session}/status")
async def get_session_status(session: str) -> dict[str, object]:
    """Снимок задачи сессии. ``resume_allowed`` совпадает с последним ``agent_status``."""
    internal = _check_session(session)
    return _for_viewer(_public(get_runtime().supervisor.session_snapshot(internal)))


@router.get("/{session}/partial-changes")
async def get_partial_changes(session: str, task_id: int) -> dict[str, object]:
    """Список ``change_id`` шагов ``partial``. Откат не выполняется."""
    internal = _check_session(session)
    runtime = get_runtime()
    row = await asyncio.to_thread(runtime.tasks.get, task_id)
    if row is None or str(row.get("session_id")) != internal:
        raise HTTPException(status_code=404, detail="not found")
    return {
        "ok": True,
        "session": session,
        "task_id": task_id,
        "change_ids": runtime.supervisor.list_partial_change_ids(task_id),
    }


@router.get("/{session}/stream")
async def stream_session(
    session: str,
    request: Request,
    project_id: int | None = Query(None, ge=1),
) -> EventSourceResponse:
    """SSE. ``Last-Event-ID`` — последний увиденный seq, replay строго после него."""
    internal = _check_session(session)
    await _assert_project_visible(project_id)
    last_header = request.headers.get("last-event-id")
    try:
        last_seq = int(last_header) if last_header else 0
    except ValueError:
        last_seq = 0

    async def _gen():
        runtime = get_runtime()
        cursor = last_seq
        async with (
            runtime.bus.subscription(internal) as queue,
            orch_bridge.notice_relay(runtime.bus, internal, project_id),
        ):
            for event in await runtime.bus.replay_from(internal, last_seq):
                cursor, fresh = remember_seq(cursor, event.seq)
                if fresh:
                    yield _sse(event)
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=_STREAM_POLL_SEC)
                except TimeoutError:
                    continue
                cursor, fresh = remember_seq(cursor, event.seq)
                if not fresh:
                    continue
                yield _sse(event)

    return EventSourceResponse(_gen(), ping=15)
