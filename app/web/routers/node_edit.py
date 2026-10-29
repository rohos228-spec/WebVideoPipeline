"""Правка ноды проекта: модель, параметры, откат (вызывает чат через слой действий).

Перенос из video-pipeline int/chat-v1. Сессия — ``get_session`` единой базы:
строки проектов закрыты RLS арендатора, чужой проект здесь — 404, как в
``projects``.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from loguru import logger
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import commit_with_retry
from app.models import Project, ProjectStatus
from app.services import node_edit as ne
from app.services.event_bus import publish_project_event
from app.web.deps import get_session

router = APIRouter(prefix="/projects", tags=["node-edit"])


class ModelBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model_id: str | None = Field(default=None, max_length=120)
    allow_costlier: bool = False


class BranchItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    edge_id: str = Field(min_length=1, max_length=120)
    kind: Literal["after", "pass", "fail", "gate"]


class ParamsBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    disabled: bool | None = None
    check_mode: bool | None = None
    check_fix: bool | None = None
    prompt_variant: str | None = Field(default=None, min_length=1, max_length=120)
    retry_profile: Literal["safe", "fast"] | None = None
    branches: list[BranchItem] | None = Field(default=None, max_length=20)
    reasoning_effort: Literal["low", "medium", "high"] | None = None
    allow_costlier: bool = False


class RestoreBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    values: dict
    expected: dict | None = None


def _actor(request: Request) -> str:
    return str(request.headers.get("X-Action-Layer-Operator") or "studio")[:120]


def _http(exc: ne.NodeEditError) -> HTTPException:
    return HTTPException(status_code=exc.http, detail=exc.to_detail())


async def _project(session: AsyncSession, project_id: int) -> Project:
    p = await session.get(Project, project_id)
    if p is None:
        raise HTTPException(
            status_code=404, detail={"code": "project_not_found", "message": "Проект не найден."}
        )
    return p


def _require_idle(p: Project) -> None:
    from app.services.project_state import is_running_status

    try:
        running = is_running_status(ProjectStatus(getattr(p.status, "value", p.status)))
    except ValueError:
        running = False
    if running:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "project_busy",
                "message": "В проекте сейчас выполняется шаг: менять ноду можно после его "
                "окончания или остановки.",
            },
        )


async def _save(session: AsyncSession, p: Project, *, graph_changed: bool) -> None:
    from sqlalchemy.orm.attributes import flag_modified

    flag_modified(p, "meta")
    if graph_changed:
        from app.services.canvas_graph import sync_run_snapshot_from_canvas_graph

        await sync_run_snapshot_from_canvas_graph(session, p)
    # Одна база (app.db): копии проекта в project.db/мастер на этой линии нет, синхронизировать нечего.
    await commit_with_retry(session)
    try:
        await publish_project_event(p.id, event_type="project_updated")
    except Exception as exc:
        logger.debug("[#{}] node_edit event: {}", p.id, exc)


async def _effective_state(session: AsyncSession, p: Project) -> tuple[dict, str | None]:
    from app.services.canvas_graph import effective_project_meta

    meta, source = await effective_project_meta(session, p)
    return {**ne.project_state_of(p), "meta": meta}, source


async def _own_graph(session: AsyncSession, p: Project) -> bool:
    from app.services.canvas_graph import materialize_project_graph

    return await materialize_project_graph(session, p)


@router.get("/{project_id}/effective-graph")
async def effective_graph(project_id: int, session: AsyncSession = Depends(get_session)) -> dict:
    from app.services.canvas_graph import resolve_effective_graph

    p = await _project(session, project_id)
    graph = await resolve_effective_graph(session, p)
    if graph is None:
        raise HTTPException(
            status_code=404, detail={"code": "graph_not_found", "message": "У проекта нет графа нод."}
        )
    return graph


@router.post("/{project_id}/own-graph")
async def own_project_graph(project_id: int, session: AsyncSession = Depends(get_session)) -> dict:
    from app.services.canvas_graph import canvas_graph_from_meta

    p = await _project(session, project_id)
    if canvas_graph_from_meta(p.meta if isinstance(p.meta, dict) else {}):
        return {"ok": True, "created": False}
    _require_idle(p)
    created = await _own_graph(session, p)
    if created:
        await _save(session, p, graph_changed=True)
    return {"ok": True, "created": created}


@router.get("/{project_id}/nodes/{node_key}/describe")
async def describe_node(project_id: int, node_key: str, session: AsyncSession = Depends(get_session)) -> dict:
    p = await _project(session, project_id)
    state, source = await _effective_state(session, p)
    try:
        out = ne.describe_node(state, node_key)
    except ne.NodeEditError as exc:
        raise _http(exc) from None
    out["graph_source"] = source
    if source == "default":
        out["text_ru"] += (
            "\nХолст проекта пока общий (по умолчанию). При первой правке ноды "
            "у проекта появится своя копия холста, общий не меняется."
        )
    return out


@router.get("/{project_id}/nodes/{node_key}/model-options")
async def model_options(project_id: int, node_key: str, session: AsyncSession = Depends(get_session)) -> dict:
    p = await _project(session, project_id)
    effective, _source = await _effective_state(session, p)
    try:
        return ne.model_options(effective["meta"], node_key, effective.get("image_generator"))
    except ne.NodeEditError as exc:
        raise _http(exc) from None


@router.patch("/{project_id}/nodes/{node_key}/model")
async def set_node_model(
    project_id: int,
    node_key: str,
    body: ModelBody,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    p = await _project(session, project_id)
    _require_idle(p)
    own = await _own_graph(session, p)
    try:
        result = await ne.set_model_on_project(
            p, node_key, body.model_id, allow_costlier=body.allow_costlier, actor=_actor(request)
        )
    except ne.NodeEditError as exc:
        raise _http(exc) from None
    await _save(session, p, graph_changed=own)
    return {"ok": True, **result}


@router.post("/{project_id}/nodes/{node_key}/model/revert")
async def revert_node_model(
    project_id: int, node_key: str, request: Request, session: AsyncSession = Depends(get_session)
) -> dict:
    p = await _project(session, project_id)
    _require_idle(p)
    own = await _own_graph(session, p)
    meta = p.meta if isinstance(p.meta, dict) else {}
    try:
        rv = ne.plan_model_revert(meta, node_key)
        new_meta, plan = ne.apply_model_change(
            meta, node_key, rv["target_model_id"], actor=_actor(request), revert_of=rv["entry"].get("at")
        )
    except ne.NodeEditError as exc:
        raise _http(exc) from None
    p.meta = new_meta
    await _save(session, p, graph_changed=own)
    return {"ok": True, **plan, "text_ru": "Возвращено. " + ne.model_change_text(plan)}


@router.patch("/{project_id}/nodes/{node_key}/params")
async def set_node_params(
    project_id: int,
    node_key: str,
    body: ParamsBody,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    p = await _project(session, project_id)
    _require_idle(p)
    own = await _own_graph(session, p)
    changes = body.model_dump(exclude={"allow_costlier"}, exclude_none=True)
    try:
        plan = ne.plan_params_change(ne.project_state_of(p), node_key, changes)
        if plan["needs_confirmation"] and not body.allow_costlier:
            raise ne.NodeEditError(
                "needs_confirmation",
                (
                    ne.params_change_text(plan) + " Это может увеличить расходы или затронуть "
                    "другие ноды: нужно подтверждение человека."
                ),
                http=409,
                details={"items": plan["items"]},
            )
        new_meta, plan = ne.apply_params_change(
            ne.project_state_of(p), node_key, changes, actor=_actor(request)
        )
    except ne.NodeEditError as exc:
        raise _http(exc) from None
    p.meta = new_meta
    await _save(session, p, graph_changed=own or "branches" in changes)
    return {"ok": True, "node_key": node_key, "items": plan["items"], "text_ru": ne.params_change_text(plan)}


@router.post("/{project_id}/nodes/{node_key}/params/revert")
async def revert_node_params(
    project_id: int, node_key: str, request: Request, session: AsyncSession = Depends(get_session)
) -> dict:
    p = await _project(session, project_id)
    _require_idle(p)
    own = await _own_graph(session, p)
    try:
        new_meta, out = ne.revert_params(ne.project_state_of(p), node_key, actor=_actor(request))
    except ne.NodeEditError as exc:
        raise _http(exc) from None
    graph_changed = own or any(i["param"] == "branches" for i in out["items"])
    p.meta = new_meta
    await _save(session, p, graph_changed=graph_changed)
    names = ", ".join(i["title_ru"] for i in out["items"])
    return {"ok": True, **out, "text_ru": f"Нода «{out['node_label']}»: возвращено ({names})."}


@router.post("/{project_id}/nodes/{node_key}/params/restore")
async def restore_node_params(
    project_id: int,
    node_key: str,
    body: RestoreBody,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    p = await _project(session, project_id)
    _require_idle(p)
    own = await _own_graph(session, p)
    try:
        new_meta, out = ne.restore_params(
            ne.project_state_of(p), node_key, body.values, expected=body.expected, actor=_actor(request)
        )
    except ne.NodeEditError as exc:
        raise _http(exc) from None
    graph_changed = own or any(i["param"] == "branches" for i in out["items"])
    p.meta = new_meta
    await _save(session, p, graph_changed=graph_changed)
    names = ", ".join(i["title_ru"] for i in out["items"])
    return {"ok": True, **out, "text_ru": f"Нода «{out['node_label']}»: возвращено ({names})."}
