"""Граф канваса проекта: nodes/edges/positions в project.meta.canvas_graph."""

from __future__ import annotations

import copy
from datetime import UTC, datetime
from typing import Any

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import NodeRun, Project, Workflow, WorkflowRun
from app.services.excel_gpt_node import effective_node_type


def canvas_graph_from_meta(meta: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(meta, dict):
        return None
    raw = meta.get("canvas_graph")
    if not isinstance(raw, dict):
        return None
    nodes = raw.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        return None
    edges = raw.get("edges")
    if not isinstance(edges, list):
        edges = []
    return {"nodes": nodes, "edges": edges, "workflow_id": raw.get("workflow_id")}


def find_canvas_node_key_by_type(meta: dict[str, Any] | None, node_type: str) -> str | None:
    """Первый node id данного type из project.meta.canvas_graph (без WorkflowRun)."""
    want = str(node_type or "").strip()
    if not want:
        return None
    cg = canvas_graph_from_meta(meta)
    if not cg:
        return None
    for n in cg.get("nodes") or []:
        if not isinstance(n, dict):
            continue
        if str(n.get("type") or "").strip() != want:
            continue
        nid = str(n.get("id") or "").strip()
        if nid:
            return nid
    return None


def build_canvas_graph_payload(
    *,
    workflow_id: int,
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "workflow_id": workflow_id,
        "nodes": nodes,
        "edges": edges,
        "saved_at": datetime.now(UTC).isoformat(),
    }


GRAPH_SOURCE_PROJECT = "project"
GRAPH_SOURCE_DEFAULT = "default"


def _graph_items(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    return [copy.deepcopy(item) for item in raw if isinstance(item, dict)]


async def _default_workflow(session: AsyncSession) -> Workflow | None:
    stmt = select(Workflow).where(Workflow.is_default.is_(True)).order_by(Workflow.id.asc()).limit(1)
    # Одна база: дефолтный Workflow лежит в той же сессии, что и проект.
    try:
        return (await session.execute(stmt)).scalars().first()
    except Exception:
        logger.debug("effective graph: default workflow lookup failed", exc_info=True)
        return None


def _pack_graph(
    nodes: Any, edges: Any, workflow_id: int | None, source: str, origin: str
) -> dict[str, Any] | None:
    items = _graph_items(nodes)
    if not items:
        return None
    return {
        "workflow_id": workflow_id,
        "nodes": items,
        "edges": _graph_items(edges),
        "source": source,
        "origin": origin,
    }


async def resolve_effective_graph(session: AsyncSession, project: Project) -> dict[str, Any] | None:
    meta = project.meta if isinstance(project.meta, dict) else {}
    own = canvas_graph_from_meta(meta)
    if own:
        return _pack_graph(
            own["nodes"],
            own["edges"],
            own.get("workflow_id"),
            GRAPH_SOURCE_PROJECT,
            "canvas_graph",
        )
    with session.no_autoflush:
        default = await _default_workflow(session)
        default_id = default.id if default is not None else None
        run = (
            (
                await session.execute(
                    select(WorkflowRun)
                    .where(WorkflowRun.project_id == project.id)
                    .order_by(WorkflowRun.id.asc())
                    .limit(1)
                )
            )
            .scalars()
            .first()
        )
        if run is not None:
            packed = _pack_graph(
                run.nodes_snapshot,
                run.edges_snapshot,
                default_id or run.workflow_id,
                GRAPH_SOURCE_DEFAULT,
                "run_snapshot",
            )
            if packed:
                return packed
            if run.workflow_id:
                wf = await session.get(Workflow, run.workflow_id)
                if wf is not None:
                    packed = _pack_graph(
                        wf.nodes,
                        wf.edges,
                        default_id or wf.id,
                        GRAPH_SOURCE_DEFAULT,
                        "run_workflow",
                    )
                    if packed:
                        return packed
    if default is not None:
        return _pack_graph(default.nodes, default.edges, default.id, GRAPH_SOURCE_DEFAULT, "default_workflow")
    return None


def with_effective_graph(meta: dict[str, Any] | None, graph: dict[str, Any] | None) -> dict[str, Any]:
    out = dict(meta) if isinstance(meta, dict) else {}
    if graph and graph.get("nodes") and not canvas_graph_from_meta(out):
        out["canvas_graph"] = build_canvas_graph_payload(
            workflow_id=graph.get("workflow_id"),
            nodes=_graph_items(graph.get("nodes")),
            edges=_graph_items(graph.get("edges")),
        )
    return out


async def effective_project_meta(
    session: AsyncSession, project: Project
) -> tuple[dict[str, Any], str | None]:
    graph = await resolve_effective_graph(session, project)
    meta = project.meta if isinstance(project.meta, dict) else {}
    return with_effective_graph(meta, graph), (graph or {}).get("source")


EFFECTIVE_GRAPH_ATTR = "_effective_canvas_graph"


async def attach_effective_graph(session: AsyncSession, project: Project) -> dict[str, Any] | None:
    meta = project.meta if isinstance(project.meta, dict) else {}
    graph = None if canvas_graph_from_meta(meta) else await resolve_effective_graph(session, project)
    setattr(project, EFFECTIVE_GRAPH_ATTR, graph)
    return graph


def project_graph(project: Any) -> dict[str, Any] | None:
    meta = getattr(project, "meta", None)
    own = canvas_graph_from_meta(meta if isinstance(meta, dict) else {})
    if own:
        return own
    attached = getattr(project, EFFECTIVE_GRAPH_ATTR, None)
    if not isinstance(attached, dict):
        return None
    return canvas_graph_from_meta({"canvas_graph": attached})


async def materialize_project_graph(session: AsyncSession, project: Project) -> bool:
    meta = project.meta if isinstance(project.meta, dict) else {}
    if canvas_graph_from_meta(meta):
        return False
    graph = await resolve_effective_graph(session, project)
    if not graph:
        return False
    project.meta = with_effective_graph(meta, graph)
    logger.info(
        "[#{}] canvas_graph: project got its own copy of the {} graph ({} nodes)",
        project.id,
        graph.get("origin"),
        len(graph.get("nodes") or []),
    )
    return True


def canvas_topology_key(
    nodes: list[dict[str, Any]] | None,
    edges: list[dict[str, Any]] | None,
) -> str:
    """Сигнатура графа без позиций — для пропуска sync при drag нод."""
    import json

    nkeys = sorted(
        (str(n.get("id") or ""), str(n.get("type") or ""))
        for n in (nodes or [])
        if isinstance(n, dict) and n.get("id")
    )
    ekeys = sorted(
        (
            str(e.get("source") or ""),
            str(e.get("target") or ""),
            str((e.get("data") or {}).get("kind") if isinstance(e.get("data"), dict) else "after") or "after",
        )
        for e in (edges or [])
        if isinstance(e, dict) and e.get("source") and e.get("target")
    )
    return json.dumps({"n": nkeys, "e": ekeys}, ensure_ascii=False, separators=(",", ":"))


async def sync_run_snapshot_from_canvas_graph(
    session: AsyncSession,
    project: Project,
    *,
    force: bool = False,
) -> bool:
    """Копирует canvas_graph проекта в WorkflowRun.nodes_snapshot (для graph planner).

    При неизменной топологии (только позиции нод) — no-op: не трогаем snapshot
    и не держим SQLite write дольше необходимого.
    """
    cg = canvas_graph_from_meta(project.meta if isinstance(project.meta, dict) else {})
    if not cg:
        return False
    nodes = list(cg["nodes"])
    edges = list(cg["edges"])
    with session.no_autoflush:
        run = (
            await session.execute(
                select(WorkflowRun)
                .where(WorkflowRun.project_id == project.id)
                .options(selectinload(WorkflowRun.node_runs))
            )
        ).scalar_one_or_none()
    if run is None:
        return False
    if not force:
        old_n = list(run.nodes_snapshot or []) if isinstance(run.nodes_snapshot, list) else []
        old_e = list(run.edges_snapshot or []) if isinstance(run.edges_snapshot, list) else []
        if canvas_topology_key(old_n, old_e) == canvas_topology_key(nodes, edges):
            logger.debug(
                "canvas_graph: skip snapshot sync run #{} (topology unchanged)",
                run.id,
            )
            return False
    run.nodes_snapshot = nodes
    run.edges_snapshot = edges
    node_ids = {str(n.get("id")) for n in nodes if n.get("id")}
    existing = {nr.node_key for nr in run.node_runs}
    for n in nodes:
        nid = n.get("id")
        if not nid or nid in existing:
            continue
        session.add(
            NodeRun(
                workflow_run_id=run.id,
                node_key=str(nid),
                node_type=effective_node_type(n),
            )
        )
    for nr in list(run.node_runs):
        if nr.node_key not in node_ids:
            await session.delete(nr)
    await session.flush()
    logger.debug(
        "canvas_graph: synced run #{} snapshot ({} nodes, {} edges)",
        run.id,
        len(nodes),
        len(edges),
    )
    return True


GRAPH_SOURCE_PROJECT = "project"


def with_effective_graph(meta: dict[str, Any] | None, graph: dict[str, Any] | None) -> dict[str, Any]:
    res = dict(meta or {})
    if graph:
        res["canvas_graph"] = graph
    return res


async def effective_project_meta(session: AsyncSession, project: Project) -> tuple[dict[str, Any], str]:
    meta = project.meta if isinstance(project.meta, dict) else {}
    return meta, GRAPH_SOURCE_PROJECT


async def materialize_project_graph(session: AsyncSession, project: Project) -> bool:
    meta = project.meta if isinstance(project.meta, dict) else {}
    return bool(canvas_graph_from_meta(meta))
