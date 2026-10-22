"""Чат оркестратора: приём сообщения, SSE-поток и решение по approval.

POST ``/api/orchestrator/chat/{session}/message``
GET  ``/api/orchestrator/chat/{session}/status``
GET  ``/api/orchestrator/chat/{session}/stream``  (Last-Event-ID)
POST ``/api/orchestrator/chat/{session_id}/approve``
GET  ``/api/orchestrator/chat/{session}/partial-changes``
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
from app.services.plain_ru import plain_ru
from app.web.engine_access import require_engine_operator

router = APIRouter(
    prefix="/orchestrator/chat",
    tags=["orchestrator-chat"],
    # Доступ по личности новой линии (IdentityMiddleware), пока только админ.
    dependencies=[Depends(require_engine_operator)],
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


def _check_session(session: str) -> str:
    if not _SESSION_RE.fullmatch(session):
        raise HTTPException(status_code=400, detail="bad session")
    return session


def remember_seq(cursor: int, seq: int) -> tuple[int, bool]:
    """Отдать seq один раз. Курсор — последнее число, не растущее множество."""
    if seq <= cursor:
        return cursor, False
    return seq, True


def _sse(event: OrchEvent) -> dict[str, str]:
    return {
        "event": event.type,
        "id": str(event.seq),
        "data": event.model_dump_json(),
    }


@router.post("/{session}/message")
async def post_message(session: str, body: MessageIn, request: Request) -> dict[str, object]:
    """Принять реплику. Ack уходит в шину до классификации, ответ HTTP её не ждёт."""
    _check_session(session)
    runtime = get_runtime()
    ack_seq = await runtime.queue.accept(
        session,
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
    _check_session(session)
    runtime = get_runtime()
    try:
        return await runtime.supervisor.bind_project(session, body.project_id, task_id=body.task_id)
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
    operator = orch_bridge.human_operator(request.scope)
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
    _check_session(session_id)
    runtime = get_runtime()
    try:
        result, _plan_id = await human_decision(
            request,
            session_id=session_id,
            change_id=body.change_id,
            decision=body.decision,
            retry_grant=body.retry_grant,
        )
    except DECISION_ERRORS as exc:
        failure = decision_failure(exc)
        if isinstance(failure, JSONResponse):
            return failure
        raise failure from None
    return _approval_result(runtime.supervisor, session_id, body, result)


@router.post("/{session}/resume")
async def post_resume(session: str) -> dict[str, object]:
    """Продолжить с чекпоинта.

    ``cancelled`` → ``executing`` — 409. Нет задачи или чекпоинта — 404.
    """
    _check_session(session)
    runtime = get_runtime()
    task_id = runtime.supervisor.active_task(session)
    if task_id is None:
        task_id = runtime.supervisor._session_task.get(session)
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
    _check_session(session)
    return get_runtime().supervisor.session_snapshot(session)


@router.get("/{session}/partial-changes")
async def get_partial_changes(session: str, task_id: int) -> dict[str, object]:
    """Список ``change_id`` шагов ``partial``. Откат не выполняется."""
    _check_session(session)
    runtime = get_runtime()
    row = runtime.tasks.get(task_id)
    if row is None or str(row.get("session_id")) != session:
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
    _check_session(session)
    last_header = request.headers.get("last-event-id")
    try:
        last_seq = int(last_header) if last_header else 0
    except ValueError:
        last_seq = 0

    async def _gen():
        runtime = get_runtime()
        cursor = last_seq
        async with (
            runtime.bus.subscription(session) as queue,
            orch_bridge.notice_relay(runtime.bus, session, project_id),
        ):
            for event in await runtime.bus.replay_from(session, last_seq):
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
