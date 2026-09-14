"""Единый реестр шагов и активных статусов конвейера (Single Source of Truth)."""

from __future__ import annotations

from app.models import ProjectStatus


def running_statuses() -> set[ProjectStatus]:
    """Полный набор running-статусов: реестр StepDef + реестр рабочих нод.

    Меню Telegram не знает про publish (нода есть, пункта меню нет), поэтому
    объединяем с ``node_registry.RUNNING_TO_NODE_TYPE`` — иначе publishing
    выпадает из «занятых» статусов очереди и пересчёта состояния.
    """
    from app.orchestrator.node_registry import RUNNING_TO_NODE_TYPE
    from app.telegram.menu import _STEP_BY_CODE, STEPS

    statuses: set[ProjectStatus] = {step.running_status for step in STEPS if step.running_status}
    statuses.update(step.running_status for step in _STEP_BY_CODE.values() if step.running_status)
    statuses.update(RUNNING_TO_NODE_TYPE)
    return statuses


def running_statuses_list() -> list[ProjectStatus]:
    """Список активных running-статусов для использования в SQL in_() выборках воркеров."""
    from app.telegram.menu import status_order

    return sorted(running_statuses(), key=lambda s: status_order(s))
