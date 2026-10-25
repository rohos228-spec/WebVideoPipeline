"""Кто дал команду через слой действий: задача чата или человек.

Номер очереди чата остаётся за чатом только если команду дал сам чат: оператор
``orchestrator:task-N`` или план, который создал чат (шаг с подтверждением человек
одобряет сам, и исполнитель идёт от его имени). Остальные вызовы слоя действий
(Studio-панель «Действия», человек) считаются человеком.
"""

from __future__ import annotations

import re

TASK_OPERATOR_RE = r"orchestrator:task-(\d+)"
PLAN_HEADER = "X-Action-Layer-Plan"


async def plan_creator(plan_id: str | None) -> str:
    """``ActionPlan.operator`` плана; пусто, если плана нет или его не прочитать."""
    if not plan_id:
        return ""
    try:
        from sqlalchemy import select

        from app.services.action_layer import store

        async with store.sessions()() as s:
            return str(
                await s.scalar(select(store.ActionPlan.operator).where(store.ActionPlan.id == plan_id)) or ""
            )
    except Exception:  # noqa: BLE001
        return ""


async def is_chat_caller(operator: str | None, plan_id: str | None = None) -> bool:
    """Вызов пришёл от задачи чата (заголовки слоя действий + проверенный токен оператора)."""
    if not operator:
        return False
    from app.services import access_guard

    if not access_guard.operator_token_verified.get():
        return False
    if re.fullmatch(TASK_OPERATOR_RE, str(operator)):
        return True
    return bool(re.fullmatch(TASK_OPERATOR_RE, await plan_creator(plan_id)))
