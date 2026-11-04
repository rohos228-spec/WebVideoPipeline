"""API: папки сайдбара, порядок проектов, очередь генерации."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.db import session_scope
from app.models import Project
from app.services import sidebar_layout as layout_svc
from app.services.gen_queue_run import clear_gen_queue_run, set_gen_queue_run
from app.services.project_control import clear_user_stop_gate

router = APIRouter(prefix="/sidebar-layout", tags=["sidebar-layout"])


class FolderCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class FolderRename(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class ProjectPlacement(BaseModel):
    folder_id: str | None = None
    order: int = 0


class LayoutUpdate(BaseModel):
    folders: list[dict] | None = None
    project_layout: dict[str, ProjectPlacement | dict] | None = None
    gen_queue: list[int] | None = None


class GenQueueToggle(BaseModel):
    project_id: int


class GenQueueEnqueue(BaseModel):
    project_id: int
    mode: Literal["full", "until_node"] = "full"
    target_node_key: str | None = None
    target_node_type: str | None = None
    # Ставит чат (action layer): id сессии чата. Аварийный стоп чата снимает с очереди
    # только номера своей сессии. ``True`` — старая метка без сессии: стоп её не трогает.
    by_chat: bool | str = False


async def _own_ids(session) -> set[int] | None:
    """Проекты автора запроса, если запрос идёт от арендатора; ``None`` — режим владельца.

    Файл очереди и раскладки сайдбара общий для студии. Под арендатором правка
    трогает только его номера, ответ показывает только их (см.
    ``gen_queue.own_project_ids``).
    """
    from app.services.gen_queue import own_project_ids

    return await own_project_ids(session)


@router.get("")
async def get_sidebar_layout() -> dict:
    from sqlalchemy import select

    from app.db import session_scope
    from app.models import Project
    from app.services.gen_queue import get_gen_queue_idle_info

    async with session_scope() as session:
        own = await _own_ids(session)
        ids = {int(pid) for pid in (await session.execute(select(Project.id))).scalars().all()}
        payload = layout_svc.layout_for_api(ids if own is None else own)
        payload["gen_queue_idle"] = await get_gen_queue_idle_info(session)
        await session.commit()
    return payload


@router.put("")
async def put_sidebar_layout(body: LayoutUpdate) -> dict:
    project_layout = None
    if body.project_layout is not None:
        project_layout = {
            k: v.model_dump() if isinstance(v, ProjectPlacement) else dict(v)
            for k, v in body.project_layout.items()
        }
    async with session_scope() as session:
        own = await _own_ids(session)
        ids = {int(pid) for pid in (await session.execute(select(Project.id))).scalars().all()}
    # Под арендатором правка трогает только его проекты: номера других клиентов в
    # очереди и их места в сайдбаре (ответ их не показывает) остаются как были.
    layout_svc.update_layout(
        folders=body.folders,
        project_layout=project_layout,
        gen_queue=body.gen_queue,
        own_ids=own,
    )
    return layout_svc.layout_for_api(ids if own is None else own)


@router.post("/folders")
async def create_folder(body: FolderCreate) -> dict:
    try:
        return layout_svc.create_folder(body.name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.patch("/folders/{folder_id}")
async def rename_folder(folder_id: str, body: FolderRename) -> dict:
    try:
        return layout_svc.rename_folder(folder_id, body.name)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.delete("/folders/{folder_id}")
async def delete_folder(folder_id: str) -> dict:
    # Под арендатором в корень уходят только его проекты (см. ``delete_folder``).
    async with session_scope() as session:
        own = await _own_ids(session)
    if not layout_svc.delete_folder(folder_id, own_ids=own):
        raise HTTPException(status_code=404, detail="folder not found")
    return {"ok": True}


@router.post("/gen-queue/toggle")
async def toggle_gen_queue(body: GenQueueToggle) -> dict:
    async with session_scope() as session:
        project = (
            await session.execute(select(Project).where(Project.id == body.project_id))
        ).scalar_one_or_none()
        if project is None:
            raise HTTPException(status_code=404, detail="project not found")
        own = await _own_ids(session)
        queue = layout_svc.get_gen_queue()
        if body.project_id in queue:
            queue = layout_svc.toggle_gen_queue(body.project_id)
            from app.services.gen_queue import on_project_removed_from_gen_queue

            await on_project_removed_from_gen_queue(session, project)
            await clear_gen_queue_run(session, project)
            await session.commit()
        else:
            raise HTTPException(
                status_code=400,
                detail="use POST /gen-queue/enqueue to add with run target",
            )
    view = layout_svc.own_queue_view(queue, own)
    return {**view, "position": view["gen_queue_positions"].get(body.project_id)}


class GenQueueBulkEnqueue(BaseModel):
    project_ids: list[int] = Field(min_length=1)
    mode: Literal["full", "until_node"] = "full"
    target_node_key: str | None = None
    target_node_type: str | None = None


@router.post("/gen-queue/bulk-enqueue")
async def bulk_enqueue_gen_queue(body: GenQueueBulkEnqueue) -> dict:
    """Поставить несколько проектов в очередь за раз (порядок = порядок в списке)."""
    async with session_scope() as session:
        projects: list[Project] = []
        for pid in body.project_ids:
            project = (await session.execute(select(Project).where(Project.id == pid))).scalar_one_or_none()
            if project is None:
                raise HTTPException(status_code=404, detail=f"project not found: {pid}")
            if not project.auto_mode:
                raise HTTPException(
                    status_code=400,
                    detail=f"Включите auto_mode для проекта #{pid}",
                )
            projects.append(project)
        for project in projects:
            clear_user_stop_gate(project)
            await set_gen_queue_run(
                session,
                project,
                mode=body.mode,
                target_node_key=body.target_node_key,
                target_node_type=body.target_node_type,
            )
        own = await _own_ids(session)
        # Свои номера очереди — ровно этот список; номера других клиентов остаются.
        layout_svc.set_own_gen_queue(body.project_ids, own)
        layout_svc.clear_gen_queue_halted(reason="bulk-enqueue")
        await session.commit()
    return layout_svc.own_queue_view(layout_svc.get_gen_queue(), own)


@router.post("/gen-queue/enqueue")
async def enqueue_gen_queue(body: GenQueueEnqueue) -> dict:
    async with session_scope() as session:
        project = (
            await session.execute(select(Project).where(Project.id == body.project_id))
        ).scalar_one_or_none()
        if project is None:
            raise HTTPException(status_code=404, detail="project not found")
        if not project.auto_mode:
            raise HTTPException(
                status_code=400,
                detail="Включите auto_mode (режим ИИ) для проекта перед добавлением в очередь",
            )
        from app.services.gen_queue import CHAT_OWNER_KEY, chat_queue_owner

        already_queued = body.project_id in layout_svc.get_gen_queue()
        prev_owner = chat_queue_owner(project) if already_queued else None
        clear_user_stop_gate(project)
        if isinstance(body.by_chat, str):
            from app.services.orch_tenancy import owns_session

            wanted_owner: str | bool | None = body.by_chat.strip() or None
            # Метка — внутреннее имя сессии чата, в нём арендатор: сессию другого
            # арендатора хозяином номера не ставим (номер тогда — человека).
            if isinstance(wanted_owner, str) and not owns_session(wanted_owner):
                wanted_owner = None
        else:
            wanted_owner = True if body.by_chat else None
        # Чат повторно ставит номер, который уже стоит в очереди, — хозяин номера не
        # меняется (человек или та сессия чата, что ставила первой). Человек ставит
        # сам — номер его, даже если раньше ставил чат.
        chat_owned = (prev_owner if already_queued else wanted_owner) if wanted_owner else None
        try:
            await set_gen_queue_run(
                session,
                project,
                mode=body.mode,
                target_node_key=body.target_node_key,
                target_node_type=body.target_node_type,
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        if chat_owned:
            meta = dict(project.meta or {})
            run = dict(meta.get("gen_queue_run") or {})
            run[CHAT_OWNER_KEY] = chat_owned
            meta["gen_queue_run"] = run
            project.meta = meta
        run_meta = (
            dict(project.meta.get("gen_queue_run") or {})
            if isinstance(project.meta, dict) and isinstance(project.meta.get("gen_queue_run"), dict)
            else None
        )
        queue = layout_svc.get_gen_queue()
        if body.project_id not in queue:
            queue = layout_svc.toggle_gen_queue(body.project_id)
        else:
            queue = layout_svc.get_gen_queue()
        layout_svc.clear_gen_queue_halted(reason=f"enqueue #{body.project_id}")
        own = await _own_ids(session)
        await session.commit()
    view = layout_svc.own_queue_view(queue, own)
    return {
        **view,
        "position": view["gen_queue_positions"].get(body.project_id),
        "gen_queue_run": run_meta,
        "already_queued": already_queued,
    }
