"""Этап 3 (E.4/E.5): дашборд стоимости LLM + поднятие бюджета.

Один дашборд в смете: по нодам прогона (вызовы / токены / $ / из них
неуспешные / unbilled количеством), сумма прогона, разбивка по моделям,
статус бюджета; тоталы по прогонам (= проектам) для динамики + срез
«adhoc» (project_id=NULL — workspace/чат оркестратора).
Все цифры — агрегаты SUM по llm_calls, строк-сумм в БД нет.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.models import LlmCall, Project
from app.services import llm_ledger
from app.web.deps import get_session

router = APIRouter(tags=["llm-costs"])

_FAILED = case(
    (LlmCall.result != "ok", 1),
    (LlmCall.contract_rejected.is_(True), 1),
    else_=0,
)
_UNBILLED = case((LlmCall.unbilled.is_(True), 1), else_=0)
_REJECTED = case((LlmCall.contract_rejected.is_(True), 1), else_=0)


def _agg_columns() -> list[Any]:
    return [
        func.count(LlmCall.id).label("calls"),
        func.coalesce(func.sum(LlmCall.prompt_tokens), 0).label("prompt_tokens"),
        func.coalesce(func.sum(LlmCall.completion_tokens), 0).label("completion_tokens"),
        func.coalesce(func.sum(LlmCall.cost_usd), 0.0).label("cost_usd"),
        func.coalesce(func.sum(_FAILED), 0).label("failed"),
        func.coalesce(func.sum(_REJECTED), 0).label("contract_rejected"),
        func.coalesce(func.sum(_UNBILLED), 0).label("unbilled"),
    ]


def _row_dict(row: Any) -> dict[str, Any]:
    return {
        "calls": int(row.calls or 0),
        "prompt_tokens": int(row.prompt_tokens or 0),
        "completion_tokens": int(row.completion_tokens or 0),
        "cost_usd": round(float(row.cost_usd or 0.0), 6),
        "failed": int(row.failed or 0),
        "contract_rejected": int(row.contract_rejected or 0),
        "unbilled": int(row.unbilled or 0),
    }


@router.get("/projects/{project_id}/llm-costs")
async def project_llm_costs(project_id: int, session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")

    where = LlmCall.project_id == project_id
    by_node = (
        await session.execute(
            select(LlmCall.node_key, *_agg_columns())
            .where(where)
            .group_by(LlmCall.node_key)
            .order_by(func.sum(LlmCall.cost_usd).desc())
        )
    ).all()
    # Фактическая модель ответа (served), fallback — запрошенная. GROUP BY
    # по выражению, не по строке "model": строку SQLite резолвит во
    # входную колонку llm_calls.model и сливает разные served в одну группу.
    model_expr = func.coalesce(func.nullif(LlmCall.served_model, ""), LlmCall.model)
    by_model = (
        await session.execute(
            select(model_expr.label("model"), *_agg_columns())
            .where(where)
            .group_by(model_expr)
            .order_by(func.sum(LlmCall.cost_usd).desc())
        )
    ).all()
    total_row = (await session.execute(select(*_agg_columns()).where(where))).one()
    total = _row_dict(total_row)

    meta = project.meta if isinstance(project.meta, dict) else {}
    budget = llm_ledger.budget_from_meta(meta)
    spent = total["cost_usd"] + llm_ledger.unpersisted_spent(project_id)
    pr = meta.get("pause_reason") if isinstance(meta.get("pause_reason"), dict) else None
    return {
        "project_id": project_id,
        "nodes": [{"node_key": r.node_key, **_row_dict(r)} for r in by_node],
        "models": [{"model": r.model, **_row_dict(r)} for r in by_model],
        "total": total,
        "budget": {
            "budget_usd": budget,
            "spent_usd": round(spent, 6),
            "enabled": budget > 0,
            "exhausted": budget > 0 and spent >= budget,
            "override": "llm_budget_usd" in meta,
            "paused_for_budget": bool(pr and pr.get("code") == llm_ledger.BUDGET_CODE),
        },
        # Ненулевой = учёт неполный (INSERT падали) — данные занижены.
        "failed_inserts": llm_ledger.failed_insert_count(),
        "unpersisted_spent_usd": round(llm_ledger.unpersisted_spent(project_id), 6),
    }


@router.get("/llm-costs/projects")
async def llm_costs_projects(
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    rows = (
        await session.execute(
            select(LlmCall.project_id, *_agg_columns())
            .group_by(LlmCall.project_id)
            .order_by(LlmCall.project_id)
        )
    ).all()
    ids = [int(r.project_id) for r in rows if r.project_id is not None]
    names: dict[int, str] = {}
    if ids:
        for pid, title in (
            await session.execute(select(Project.id, Project.title).where(Project.id.in_(ids)))
        ).all():
            names[int(pid)] = str(title or "")
    projects = []
    adhoc: dict[str, Any] | None = None
    for r in rows:
        item = _row_dict(r)
        if r.project_id is None:
            adhoc = item
            continue
        projects.append({"project_id": int(r.project_id), "title": names.get(int(r.project_id), ""), **item})
    return {
        "projects": projects,
        "adhoc": adhoc
        or {
            "calls": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "cost_usd": 0.0,
            "failed": 0,
            "contract_rejected": 0,
            "unbilled": 0,
        },
        "failed_inserts": llm_ledger.failed_insert_count(),
    }


@router.post("/projects/{project_id}/llm-budget")
async def set_llm_budget(
    project_id: int,
    budget_usd: Annotated[float, Body(embed=True)],
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Решение оператора после паузы по бюджету: поднять бюджет проекта.

    Пишет meta["llm_budget_usd"] (0 = выключить для проекта), чистит
    pause_reason с code=budget_exhausted. Статус paused снимается
    существующим ▶ (как у vision-decision этапа 4); перезапуск без
    поднятия — повторная пауза на первом же вызове.
    """
    if budget_usd < 0:
        raise HTTPException(status_code=400, detail="budget_usd must be >= 0")
    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    meta = dict(project.meta or {})
    meta["llm_budget_usd"] = float(budget_usd)
    pr = meta.get("pause_reason")
    cleared = False
    if isinstance(pr, dict) and pr.get("code") == llm_ledger.BUDGET_CODE:
        meta.pop("pause_reason", None)
        cleared = True
    project.meta = meta
    flag_modified(project, "meta")
    await session.commit()
    llm_ledger.invalidate_budget_cache(project_id)
    return {
        "project_id": project_id,
        "budget_usd": float(budget_usd),
        "pause_reason_cleared": cleared,
    }
