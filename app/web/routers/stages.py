"""REST: /api/projects/{id}/stages — семь стадий для простого интерфейса.

Стадии — это свёртка цепочки статусов (см. app/services/pipeline_stages.py).
Здесь только веб-слой: состояние + цена одним запросом и запуск стадии.
"""

from __future__ import annotations

from decimal import ROUND_UP, Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Project
from app.services.event_bus import publish_project_event
from app.services.pipeline_stages import (
    STAGE_BY_ID,
    begin_stage_run,
    clear_stage_run,
    entry_step_code,
    stage_run_meta,
    stage_states,
)
from app.services.project_steps import start_step
from app.services.prompt_library import STEP_FOLDERS, STEP_HUMAN_NAMES
from app.services.run_sync import sync_run_for_project
from app.web.deps import get_session
from app.web.project_dto import project_to_detail

router = APIRouter(prefix="/projects", tags=["stages"])


def _credits(micro: int) -> str:
    """Микро-доллары себестоимости → кредиты для показа (округление вверх)."""
    return str(Decimal(micro or 0).scaleb(-6).quantize(Decimal("0.001"), rounding=ROUND_UP))


async def _project_or_404(session: AsyncSession, project_id: int) -> Project:
    p = await session.get(Project, project_id)
    if p is None:
        raise HTTPException(status_code=404, detail="проект не найден")
    return p


async def _prices(session: AsyncSession, project: Project) -> dict[str, dict[str, Any]]:
    """Сметы всех шагов проекта: {код шага → {price_micro, hold_micro, exact}}."""
    from app.orchestrator.step_dependencies import TOPO_ORDER
    from app.services.quote import quote_step
    from app.web.routers.billing import _cascade_volume

    frames, chars = await _cascade_volume(session, project.id)
    out: dict[str, dict[str, Any]] = {}
    for code in TOPO_ORDER:
        try:
            est = await quote_step(project, code, frames=frames, voice_chars=chars, session=session)
        except Exception:  # noqa: BLE001
            continue
        out[code] = {
            "price_micro": est.price_micro,
            "hold_micro": est.hold_micro,
            "exact": est.exact,
        }
    return out


@router.get("/{project_id}/stages")
async def list_stages(project_id: int, session: AsyncSession = Depends(get_session)) -> dict:
    """Семь стадий: состояние, цена, что правится. Один запрос на экран."""
    from app.services.project_state import recompute_status

    p = await _project_or_404(session, project_id)
    await recompute_status(session, p, log_prefix="recompute(stages)")
    await session.commit()
    await session.refresh(p)

    from app.services.project_graph import load_project_graph, node_states, stage_nodes

    prices = await _prices(session, p)
    run = stage_run_meta(p)
    # Стадии — представление графа: узлы карточки берутся с холста проекта,
    # и выключенный там узел здесь виден выключенным, а не «следующим».
    graph = await load_project_graph(session, p)
    nodes_by_stage = stage_nodes(graph, node_states(p, graph))
    stages: list[dict] = []
    total_micro = 0
    for st in stage_states(p, graph):
        price_micro = sum(int(prices.get(k, {}).get("price_micro") or 0) for k in st.price_keys)
        exact = all(bool(prices.get(k, {}).get("exact")) for k in st.price_keys if k in prices)
        if st.state not in ("done", "skipped"):
            total_micro += price_micro
        stage_items = nodes_by_stage.get(st.stage.id, [])
        for item in stage_items:
            code = item.get("step_code")
            price = prices.get(code or "", {}) if code else {}
            item["price_micro"] = int(price.get("price_micro") or 0)
            item["price_credits"] = _credits(item["price_micro"])
            item["has_prompt"] = bool(code and code in STEP_FOLDERS)
        stages.append(
            {
                "id": st.stage.id,
                "label": st.stage.label,
                "hint": st.stage.hint,
                "editor": st.stage.editor,
                # Промты стадии — с человеческими именами шагов. Голый код
                # («img_pr») пользователю ничего не говорит, а собирать
                # словарь на фронте значит держать вторую копию карты.
                "prompts": [
                    {"step": code, "label": STEP_HUMAN_NAMES.get(code, code)}
                    for code in st.stage.prompt_steps
                    if code in STEP_FOLDERS
                ],
                "state": st.state,
                "target_status": st.target.value,
                "price_micro": price_micro,
                "price_credits": _credits(price_micro),
                "exact": exact,
                "active": bool(run and run.get("stage") == st.stage.id),
                "nodes": stage_items,
            }
        )
    from app.services.project_graph import proposal_from_meta

    return {
        "project_id": p.id,
        "status": p.status.value,
        "generation_active": bool(getattr(p, "generation_active", False)),
        "stage_run": run,
        "stages": stages,
        "graph_source": graph.source,
        "graph_proposal": proposal_from_meta(p),
        "remaining_micro": total_micro,
        "remaining_credits": _credits(total_micro),
    }


@router.post("/{project_id}/stages/{stage_id}/run")
async def run_stage(
    project_id: int,
    stage_id: str,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Прогнать стадию целиком: старт первого шага + цель для авто-продвижения."""
    stage = STAGE_BY_ID.get(stage_id)
    if stage is None:
        raise HTTPException(status_code=404, detail=f"неизвестная стадия: {stage_id}")
    p = await _project_or_404(session, project_id)
    from app.services.pipeline_stages import stage_is_skipped
    from app.services.project_graph import load_project_graph

    graph = await load_project_graph(session, p)
    if stage_is_skipped(graph, stage.id):
        raise HTTPException(
            status_code=400, detail="стадия выключена на схеме — включите её узлы или уберите из схемы"
        )

    begin_stage_run(p, stage)
    code = entry_step_code(p, stage, graph)
    try:
        await start_step(session, p, code, require_node_fsm=False, explicit_ui_start=True)
    except ValueError as e:
        clear_stage_run(p)
        await session.commit()
        raise HTTPException(status_code=400, detail=str(e)) from e
    await session.commit()
    await session.refresh(p)
    await sync_run_for_project(project_id)
    await publish_project_event(
        project_id,
        event_type="stage_started",
        payload={"stage": stage.id, "step": code, "status": p.status.value},
    )
    logger.info("[#{}] стадия {} запущена шагом {}", project_id, stage.id, code)
    return {"project": project_to_detail(p), "stage": stage.id, "step": code}


@router.post("/{project_id}/stages/stop")
async def stop_stage(project_id: int, session: AsyncSession = Depends(get_session)) -> dict:
    """Остановить текущую стадию: снять цель и погасить активный шаг."""
    from app.services.project_control import stop_project_running
    from app.services.step_cancel import request_stop

    request_stop(project_id)
    p = await _project_or_404(session, project_id)
    clear_stage_run(p)
    info = await stop_project_running(session, p)
    await session.commit()
    await sync_run_for_project(project_id)
    await session.refresh(p)
    await publish_project_event(
        project_id,
        event_type="stage_stopped",
        payload={"status": p.status.value},
    )
    return {"project": project_to_detail(p), "message": info.get("message", "")}
