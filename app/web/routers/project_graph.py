"""REST: /api/projects/{id}/graph — граф ролика, а не шаблон.

`/api/workflows` правит глобальную схему-чертёж; проект по ней только
создаётся. Всё, что происходит с роликом дальше, идёт по его собственному
графу в `meta.canvas_graph`, и до этого роутера у web-интерфейса не было
ручки, которая читает и меняет именно его. Конструктор рисовал шаблон, а
исполнялось другое.

Правка идёт в два шага — `diff` (посмотреть, что изменится и что сгорит)
и `PUT` (применить). Один шаг здесь невозможен: применение графа на живом
проекте сбрасывает сделанные шаги, и молча сделать это за человека нельзя.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Project
from app.services.event_bus import publish_project_event
from app.services.project_graph import (
    STAGE_OF_NODE_TYPE,
    GraphError,
    ProjectGraph,
    apply_project_graph,
    apply_proposal,
    describe_graph,
    discard_proposal,
    graph_diff,
    load_project_graph,
    node_states,
    normalize_graph,
    proposal_from_meta,
    reset_plan,
    reset_project_graph_to_default,
)
from app.web.deps import get_session

router = APIRouter(prefix="/projects", tags=["project-graph"])


class GraphBody(BaseModel):
    nodes: list[dict[str, Any]]
    edges: list[dict[str, Any]] = Field(default_factory=list)


class GraphApplyBody(GraphBody):
    #: Сбрасывать ли устаревшие шаги. По умолчанию да: граф без сброса
    #: оставил бы результаты, сделанные по другой схеме.
    reset: bool = True


class ProposalApplyBody(BaseModel):
    proposal_id: str
    reset: bool = True


async def _project_or_404(session: AsyncSession, project_id: int) -> Project:
    p = await session.get(Project, project_id)
    if p is None:
        raise HTTPException(status_code=404, detail="проект не найден")
    return p


def _catalog() -> list[dict[str, Any]]:
    from app.orchestrator.node_registry import (
        CONFIG_NODE_TYPES,
        HITL_NODE_TYPES,
        NODE_TYPE_TO_STEP_CODE,
        WORK_NODES,
    )
    from app.services.prompt_library import STEP_FOLDERS
    from app.web.routers.workflows import NODE_LABELS

    def entry(node_type: str, kind: str) -> dict[str, Any]:
        step = NODE_TYPE_TO_STEP_CODE.get(node_type)
        return {
            "type": node_type,
            "label": NODE_LABELS.get(node_type, node_type),
            "kind": kind,
            "step_code": step,
            "has_prompt": bool(step and step in STEP_FOLDERS),
            "stage": STAGE_OF_NODE_TYPE.get(node_type),
        }

    out = [entry(t, "config") for t in sorted(CONFIG_NODE_TYPES)]
    out += [entry(t, "work") for t in WORK_NODES]
    out.append(entry("excel_gpt", "work"))
    out += [entry(t, "hitl") for t in sorted(HITL_NODE_TYPES)]
    return out


def _models() -> dict[str, list[dict[str, Any]]]:
    """Модели на выбор по роду узла. Каталог живёт на сервере, фронт его не знает."""
    from app.services.vibecode_catalog import models_for_channel

    out: dict[str, list[dict[str, Any]]] = {"text": [], "image": [], "video": []}
    try:
        for m in models_for_channel():
            kind = str(m.get("kind") or "text")
            if kind not in out:
                continue
            out[kind].append({"id": m["id"], "label": m.get("label") or m["id"], "vendor": m.get("vendor")})
    except Exception:  # noqa: BLE001 — без каталога выбор модели просто пуст
        pass
    return out


def _voices() -> list[dict[str, str]]:
    """Голоса озвучки — каталог живёт на сервере, инспектор его только показывает."""
    from app.services.elevenlabs_voices import ELEVENLABS_VOICES

    return [dict(v) for v in ELEVENLABS_VOICES]


def _settings(project: Project) -> dict[str, Any]:
    """Настройки узлов, которые хранятся не в графе, а в `meta` проекта.

    Параметры шага (`node_step_params`), какие проверки делает GPT перед
    авто-апрувом (`auto_review_kinds`), автопродвижение и промт, назначенный
    узлу (`prompt_slot_variants`). Инспектор узла правит их через
    `PATCH /projects/{id}`, а читает отсюда — иначе ему пришлось бы тянуть
    весь `meta`, где лежат и результаты, и снимки.
    """
    from app.services.prompt_library import node_prompt_variants

    meta = project.meta if isinstance(project.meta, dict) else {}
    raw_params = meta.get("node_step_params")
    params = (
        {k: dict(v) for k, v in raw_params.items() if isinstance(v, dict)}
        if isinstance(raw_params, dict)
        else {}
    )
    kinds = meta.get("auto_review_kinds")
    return {
        "step_params": params,
        "auto_review_kinds": [str(k) for k in kinds] if isinstance(kinds, list) else None,
        "ai_new_window_per_check": bool(meta.get("ai_new_window_per_check")),
        "auto_mode": bool(project.auto_mode),
        "bgm_level": meta.get("bgm_level"),
        "prompt_variants": node_prompt_variants(meta),
    }


async def _prices(session: AsyncSession, project: Project) -> dict[str, dict[str, Any]]:
    from app.web.routers.stages import _prices as stage_prices

    return await stage_prices(session, project)


def _payload(project: Project, graph: ProjectGraph, prices: dict[str, dict[str, Any]]) -> dict[str, Any]:
    from app.web.routers.stages import _credits
    from app.web.routers.workflows import scene_agent_choices

    states = node_states(project, graph)
    return {
        "project_id": project.id,
        "status": project.status.value,
        "source": graph.source,
        "workflow_id": graph.workflow_id,
        "nodes": graph.nodes,
        "edges": graph.edges,
        "states": states,
        "prices": {
            code: {**p, "price_credits": _credits(int(p.get("price_micro") or 0))}
            for code, p in prices.items()
        },
        "catalog": _catalog(),
        # Роль узла в веере сцен: список тот же, что в палитре шаблона —
        # маркер живёт в данных узла и едет с графом, а не в meta ролика.
        "scene_agents": scene_agent_choices(),
        "models": _models(),
        "voices": _voices(),
        "settings": _settings(project),
        "proposal": proposal_from_meta(project),
    }


@router.get("/{project_id}/graph")
async def get_graph(project_id: int, session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    """Граф проекта: узлы, связи, состояния, цены, каталог, предложение агента."""
    p = await _project_or_404(session, project_id)
    graph = await load_project_graph(session, p)
    prices = await _prices(session, p)
    return _payload(p, graph, prices)


@router.post("/{project_id}/graph/diff")
async def diff_graph(
    project_id: int, body: GraphBody, session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    """Что изменится, если применить такой граф, и какие шаги сгорят."""
    p = await _project_or_404(session, project_id)
    try:
        nodes, edges, check = normalize_graph(body.nodes, body.edges)
    except GraphError as exc:
        raise HTTPException(status_code=400, detail={"graph": exc.errors}) from exc
    current = await load_project_graph(session, p)
    diff = graph_diff(current.nodes, current.edges, nodes, edges)
    plan = reset_plan(p, current.nodes, nodes, diff)
    return {
        "valid": check["valid"],
        "errors": check["errors"],
        "warnings": check.get("warnings") or [],
        "diff": diff.to_dict(),
        "reset": plan.to_dict(),
    }


@router.put("/{project_id}/graph")
async def put_graph(
    project_id: int, body: GraphApplyBody, session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    """Применить граф проекту. Сброс устаревших шагов — по флагу `reset`."""
    p = await _project_or_404(session, project_id)
    try:
        result = await apply_project_graph(session, p, body.nodes, body.edges, reset=body.reset, source="ui")
    except GraphError as exc:
        raise HTTPException(status_code=400, detail={"graph": exc.errors}) from exc
    await _after_change(session, p, result)
    return result


@router.post("/{project_id}/graph/reset")
async def reset_graph(project_id: int, session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    """Вернуть штатную схему. Путь назад, если конструктором всё сломали."""
    p = await _project_or_404(session, project_id)
    result = await reset_project_graph_to_default(session, p)
    await _after_change(session, p, result)
    return result


@router.post("/{project_id}/graph/proposal/apply")
async def apply_graph_proposal(
    project_id: int,
    body: ProposalApplyBody,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Применить предложение агента. Кнопка, а не разговор: согласие доезжает буквой."""
    p = await _project_or_404(session, project_id)
    try:
        result = await apply_proposal(session, p, body.proposal_id, reset=body.reset)
    except GraphError as exc:
        raise HTTPException(status_code=400, detail={"graph": exc.errors}) from exc
    await _after_change(session, p, result)
    return result


@router.delete("/{project_id}/graph/proposal", status_code=204)
async def discard_graph_proposal(project_id: int, session: AsyncSession = Depends(get_session)) -> None:
    p = await _project_or_404(session, project_id)
    discard_proposal(p)
    await session.commit()
    await publish_project_event(project_id, event_type="graph_proposal_discarded")


@router.get("/{project_id}/graph/brief")
async def graph_brief(project_id: int, session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    """Компактный вид — тот же, что видит агент. Для отладки и карточек."""
    p = await _project_or_404(session, project_id)
    graph = await load_project_graph(session, p)
    return describe_graph(p, graph)


@router.get("/elevenlabs/voices-status")
async def elevenlabs_voices_status() -> dict[str, Any]:
    """Живость голосов ElevenLabs: GET /v1/voices (бесплатно, кэш 1ч).

    `checked: false` — проверить не удалось (нет ключа/сети), тогда UI
    ничего не красит. `dead` — id из каталога, которых нет в ответе API.
    """
    from app.services.elevenlabs_voices import ELEVENLABS_VOICES, probe_live_voice_ids

    live = await probe_live_voice_ids()
    if live is None:
        return {"checked": False, "live": [], "dead": []}
    known = [v["id"] for v in ELEVENLABS_VOICES]
    return {"checked": True, "live": sorted(live), "dead": [i for i in known if i not in live]}


async def _after_change(session: AsyncSession, project: Project, result: dict[str, Any]) -> None:
    """Общий хвост любой правки графа: зафиксировать, досинхронизировать, сообщить."""
    from app.services.run_sync import sync_run_for_project

    await session.commit()
    await sync_run_for_project(project.id)
    await publish_project_event(
        project.id,
        event_type="graph_changed",
        payload={"summary": (result.get("diff") or {}).get("summary"), "reset": result.get("reset")},
    )
