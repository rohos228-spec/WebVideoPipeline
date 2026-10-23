"""Общая механика: новая попытка шага заменяет его выход, не дописывает поверх.

Вызывать при входе в running (ручной ▶ и auto-advance). Повтор того же
running-такта (воркер, soft retry) сюда не попадает — статус уже running.
"""

from __future__ import annotations

from typing import Any

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Project, ProjectStatus


def step_code_for_running_status(status: ProjectStatus | None) -> str | None:
    """running-статус проекта → step_code, чей выход надо заменить."""
    if status is None:
        return None
    from app.orchestrator.node_registry import (
        NODE_TYPE_TO_STEP_CODE,
        RUNNING_TO_NODE_TYPE,
    )
    from app.services.excel_gpt_node import (
        EXCEL_GPT_STEP_CODE,
        slot_from_running_status,
    )

    if slot_from_running_status(status) is not None:
        return EXCEL_GPT_STEP_CODE
    node_type = RUNNING_TO_NODE_TYPE.get(status)
    if not node_type:
        return None
    return NODE_TYPE_TO_STEP_CODE.get(node_type)


async def replace_step_outputs_on_enter(
    session: AsyncSession,
    project: Project,
    running_status: ProjectStatus,
) -> dict[str, Any]:
    """Стереть выход шага, в который сейчас входим, чтобы GPT/ген писал заново."""
    from app.services.reset_step import clear_step_outputs_for_rerun

    if getattr(project, "id", None) is None:
        return {}
    step_code = step_code_for_running_status(running_status)
    if not step_code:
        return {}
    # Ключ активной excel_gpt — не выход шага, а «какую ноду входим»: его только что
    # поставил auto_advance._pin_excel_gpt_key_after_ready. Wipe слота его снимает, и
    # шаг брал ключ заново — бывало, ноду прошлого слота (enriching_2 «пропускал GPT»).
    pinned_key = None
    if step_code == "excel_gpt" and isinstance(project.meta, dict):
        pinned_key = str(project.meta.get("active_excel_gpt_node_key") or "").strip() or None
        from app.services.excel_gpt_node import (
            active_key_belongs_to_other_slot,
            slot_from_running_status,
        )

        slot = slot_from_running_status(running_status)
        if slot is None or active_key_belongs_to_other_slot(project, pinned_key, slot):
            pinned_key = None
    try:
        summary = await clear_step_outputs_for_rerun(session, project, step_code, force_wipe=True)
        if pinned_key:
            from app.services.project_meta import set_meta_fields

            set_meta_fields(project, active_excel_gpt_node_key=pinned_key)
    except Exception:  # noqa: BLE001
        logger.exception(
            "[#{}] replace_step_outputs_on_enter: wipe {} failed",
            project.id,
            step_code,
        )
        return {"error": step_code}
    logger.info(
        "[#{}] replace_step_outputs_on_enter: status={} step={} wiped={}",
        project.id,
        running_status.value,
        step_code,
        list(summary.keys()),
    )
    return summary
