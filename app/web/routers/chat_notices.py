"""Уведомления чата оркестратора (лента под чатом): список и «прочитано».

Перенос из video-pipeline int/chat-v1. Таблица ``chat_notices`` — арендаторская
(``tenant_tables.ORCH_RAW_TENANT_TABLES``, RLS ревизии 0020): соединение движка
привязано к арендатору запроса, участник видит и отмечает только свои
уведомления. Префикс открыт участнику в ``identity.TENANT_ALLOWED_PREFIXES``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.services import chat_notice
from app.web.engine_access import require_engine_member

router = APIRouter(
    prefix="/chat-notices", tags=["orchestrator"], dependencies=[Depends(require_engine_member)]
)


@router.get("")
async def list_chat_notices(
    project_id: int | None = Query(None, ge=1),
    since_id: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    include_global: bool = Query(True),
    unread_only: bool = Query(False),
) -> dict:
    items = await chat_notice.list_notices(
        project_id, since_id, limit, include_global=include_global, unread_only=unread_only
    )
    last_id = max([since_id, *(n["id"] for n in items)])
    return {"notices": items, "count": len(items), "last_id": last_id}


@router.post("/{notice_id}/read")
async def read_chat_notice(notice_id: int) -> dict:
    item = await chat_notice.mark_read(notice_id)
    if item is None:
        raise HTTPException(404, "Уведомление не найдено")
    try:
        from app.services.event_bus import get_bus
        from app.services.tenant import current_tenant

        evt = {
            "type": "chat_notice_read",
            "project_id": item["project_id"],
            "notice_id": item["id"],
            "tenant_id": current_tenant(),
        }
        if item["project_id"] is not None:
            await get_bus().publish(f"projects.{item['project_id']}", evt)
        await get_bus().publish("global", evt)
    except Exception:  # noqa: BLE001 - отметка уже записана, событие шины — подсказка UI
        pass
    return {"notice": item}
