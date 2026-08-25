"""REST: /api/workflows."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Workflow
from app.orchestrator.default_graph import LAYOUT_VERSION
from app.orchestrator.default_graph import default_graph as _default_graph
from app.orchestrator.graph.validate import validate_workflow_graph
from app.services.excel_gpt_node import assign_slot_indices, migrate_enrich_nodes
from app.services.workflow_run_sync import sync_runs_from_workflow
from app.web.deps import get_session
from app.web.schemas import (
    WorkflowDetail,
    WorkflowSaveRequest,
    WorkflowSummary,
)

router = APIRouter(prefix="/workflows", tags=["workflows"])


# Человеческие имена типов узлов. Держать их на фронте значило бы завести
# вторую копию реестра, которая молча разъедется с первой: тип добавят в
# `node_registry`, а подпись — нет, и в палитре появится «sfx_gen».
NODE_LABELS: dict[str, str] = {
    "topic": "Тема",
    "storage": "Хранилище",
    "plan": "Сценарий",
    "script": "Закадровый текст",
    "split": "Разбивка на кадры",
    "scene_design": "Дизайн сцен (старый)",
    "sd_agent": "Агент сцен",
    "sd_assemble": "Сборка сцен",
    "excel_gpt": "Работа с GPT",
    # Слоты доработки таблицы. Номер в подписи важнее позиции в конвейере:
    # слоты одинаковые, и различает их только он.
    "enrich_1": "Доработка №1",
    "enrich_2": "Доработка №2",
    "enrich_3": "Доработка №3",
    "enrich_4": "Доработка №4",
    "enrich_5": "Доработка №5",
    "hero": "Персонажи",
    "items": "Предметы",
    "image_prompts": "Промты картинок",
    "images": "Картинки",
    "animation_prompts": "Промты анимации",
    "videos": "Видео",
    "audio": "Озвучка",
    "music": "Музыка",
    "sfx_plan": "План звуков",
    "sfx_gen": "Звуки",
    "assemble": "Сборка ролика",
    "publish": "Публикация",
    "hitl_hero": "Проверка: персонажи",
    "hitl_images": "Проверка: картинки",
    "hitl_videos": "Проверка: видео",
    "hitl_final": "Проверка: финал",
    "hitl_gate": "Проверка человеком",
}

#: Разделы палитры. Порядок — порядок появления в конвейере, а не алфавит:
#: палитрой пользуются, чтобы дособрать цепочку, и искать узел проще там, где
#: он стоит по смыслу.
NODE_KINDS: dict[str, str] = {
    "work": "Шаги",
    "hitl": "Проверки",
    "config": "Настройка",
}


@router.get("/catalog")
async def node_catalog() -> dict:
    """Типы узлов, доступные конструктору.

    Отдаётся с сервера, потому что реестр узлов живёт здесь: у типа есть код
    шага, running- и ready-статус, и всё это фронт восстановить не может.
    """
    from app.orchestrator.node_registry import (
        CONFIG_NODE_TYPES,
        HITL_NODE_TYPES,
        NODE_TYPE_TO_STEP_CODE,
        WORK_NODES,
    )
    from app.services.prompt_library import STEP_FOLDERS

    def entry(node_type: str, kind: str) -> dict:
        step = NODE_TYPE_TO_STEP_CODE.get(node_type)
        return {
            "type": node_type,
            "label": NODE_LABELS.get(node_type, node_type),
            "kind": kind,
            "step_code": step,
            # Есть ли у узла промт — от этого зависит, показывать ли в
            # инспекторе вкладку правки текста или честное «нечего править».
            "has_prompt": bool(step and step in STEP_FOLDERS),
        }

    nodes = [entry(t, "config") for t in sorted(CONFIG_NODE_TYPES)]
    nodes += [entry(t, "work") for t in WORK_NODES]
    nodes += [entry(t, "hitl") for t in sorted(HITL_NODE_TYPES)]
    return {"kinds": NODE_KINDS, "nodes": nodes}


@router.get("", response_model=list[WorkflowSummary])
async def list_workflows(session: AsyncSession = Depends(get_session)) -> list[Workflow]:
    rows = (await session.execute(select(Workflow).order_by(Workflow.id.desc()))).scalars().all()
    return list(rows)


@router.get("/{workflow_id}", response_model=WorkflowDetail)
async def get_workflow(workflow_id: int, session: AsyncSession = Depends(get_session)) -> Workflow:
    wf = await session.get(Workflow, workflow_id)
    if wf is None:
        raise HTTPException(status_code=404, detail="workflow not found")
    return wf


@router.post("/validate")
async def validate_workflow(payload: WorkflowSaveRequest) -> dict:
    """Проверить граф без сохранения (циклы, битые связи)."""
    nodes = [n.model_dump() for n in payload.nodes]
    edges = [e.model_dump() for e in payload.edges]
    return validate_workflow_graph(nodes, edges)


@router.post("", response_model=WorkflowDetail, status_code=status.HTTP_201_CREATED)
async def create_workflow(
    payload: WorkflowSaveRequest,
    session: AsyncSession = Depends(get_session),
) -> Workflow:
    nodes_raw = [n.model_dump() for n in payload.nodes]
    edges_raw = [e.model_dump() for e in payload.edges]
    nodes_raw = assign_slot_indices(migrate_enrich_nodes(nodes_raw))
    check = validate_workflow_graph(nodes_raw, edges_raw)
    if not check["valid"]:
        raise HTTPException(status_code=400, detail={"graph": check["errors"]})
    edges_raw = check.get("edges") or edges_raw
    wf = Workflow(
        name=payload.name or "Untitled",
        description=payload.description,
        nodes=nodes_raw,
        edges=edges_raw,
        is_default=False,
        version=1,
    )
    session.add(wf)
    await session.commit()
    await session.refresh(wf)
    return wf


@router.put("/{workflow_id}", response_model=WorkflowDetail)
async def update_workflow(
    workflow_id: int,
    payload: WorkflowSaveRequest,
    session: AsyncSession = Depends(get_session),
) -> Workflow:
    wf = await session.get(Workflow, workflow_id)
    if wf is None:
        raise HTTPException(status_code=404, detail="workflow not found")
    if payload.name is not None:
        wf.name = payload.name
    if payload.description is not None:
        wf.description = payload.description
    nodes_raw = [n.model_dump() for n in payload.nodes]
    edges_raw = [e.model_dump() for e in payload.edges]
    nodes_raw = assign_slot_indices(migrate_enrich_nodes(nodes_raw))
    check = validate_workflow_graph(nodes_raw, edges_raw)
    if not check["valid"]:
        raise HTTPException(status_code=400, detail={"graph": check["errors"]})
    edges_raw = check.get("edges") or edges_raw
    wf.nodes = nodes_raw
    wf.edges = edges_raw
    wf.version = (wf.version or 1) + 1
    meta = dict(wf.meta or {})
    meta["layout_version"] = LAYOUT_VERSION
    wf.meta = meta
    await sync_runs_from_workflow(session, wf)
    await session.commit()
    await session.refresh(wf)
    return wf


@router.delete("/{workflow_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_workflow(
    workflow_id: int,
    session: AsyncSession = Depends(get_session),
) -> None:
    wf = await session.get(Workflow, workflow_id)
    if wf is None:
        raise HTTPException(status_code=404, detail="workflow not found")
    if wf.is_default:
        raise HTTPException(status_code=400, detail="cannot delete default workflow")
    await session.delete(wf)
    await session.commit()


@router.post("/{workflow_id}/duplicate", response_model=WorkflowDetail)
async def duplicate_workflow(
    workflow_id: int,
    session: AsyncSession = Depends(get_session),
) -> Workflow:
    src = await session.get(Workflow, workflow_id)
    if src is None:
        raise HTTPException(status_code=404, detail="workflow not found")
    copy = Workflow(
        name=f"{src.name} (копия)",
        description=src.description,
        nodes=list(src.nodes or []),
        edges=list(src.edges or []),
        is_default=False,
        version=1,
    )
    session.add(copy)
    await session.commit()
    await session.refresh(copy)
    return copy


@router.post("/default/reset", response_model=WorkflowDetail)
async def reset_default(session: AsyncSession = Depends(get_session)) -> Workflow:
    """Сбросить дефолтный workflow к фабричному графу (полезно после правок)."""
    from app.orchestrator.default_graph import LAYOUT_VERSION

    wf = (
        await session.execute(select(Workflow).where(Workflow.is_default == True))  # noqa: E712
    ).scalar_one_or_none()
    nodes, edges = _default_graph()
    if wf is None:
        wf = Workflow(
            name="Стандартный shorts-пайплайн",
            description="Дефолтный шаблон.",
            nodes=nodes,
            edges=edges,
            is_default=True,
            version=1,
            meta={"layout_version": LAYOUT_VERSION},
        )
        session.add(wf)
    else:
        wf.nodes = nodes
        wf.edges = edges
        wf.version = (wf.version or 1) + 1
        meta = dict(wf.meta or {})
        meta["layout_version"] = LAYOUT_VERSION
        wf.meta = meta
    await sync_runs_from_workflow(session, wf)
    await session.commit()
    await session.refresh(wf)
    return wf
