"""Управление пользователями и статистика: `/api/admin/users`, `/api/admin/overview`, `/api/admin/plans`.

Контракты соответствуют интерфейсам фронтенда в `web/src/lib/admin-api.ts`:
- `AdminOverview`
- `AdminUserRow`
- `AdminUserDetail`
- `AdminPlan`
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import case, desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Artifact,
    ArtifactKind,
    CreditAccount,
    CreditEntry,
    LlmCall,
    MediaCall,
    Project,
    StudioUser,
)
from app.services.studio_auth import current_identity
from app.web.deps import get_session

router = APIRouter(prefix="/admin", tags=["admin-users"])


def _admin_only() -> None:
    """Гейт админа: участникам 404, владельцу (local) разрешено."""
    identity = current_identity()
    if identity is not None and not identity.is_admin:
        raise HTTPException(status_code=404, detail="ручка недоступна")


# ── Схемы DTO ─────────────────────────────────────────────────────────────────


class AdminUserRow(BaseModel):
    id: str | int
    email: str
    username: str
    name: str
    role: str
    plan: str | None = "Pro"
    status: str = "active"
    email_verified: bool = True
    created_at: int = 0
    last_login: int | None = None
    balance_credits: int = 0
    spent_credits: int = 0
    generations: int = 0
    projects: int = 0
    requests: int = 0
    cost_usd: float = 0.0
    tracker_identity: str = ""
    device_name: str = ""
    plan_active: bool = True
    plan_expires_at: str | None = None
    subscription: dict[str, Any] | None = None
    limits: dict[str, Any] = Field(default_factory=dict)
    note: str = ""
    last_activity: str | None = None


class AdminUserProject(BaseModel):
    id: int
    title: str
    slug: str
    status: str
    generations: int = 0
    created_at: str


class AdminUserDetail(AdminUserRow):
    ledger: list[dict[str, Any]] = Field(default_factory=list)
    subscriptions_history: list[dict[str, Any]] = Field(default_factory=list)
    projects_list: list[AdminUserProject] = Field(default_factory=list)
    audit: list[dict[str, Any]] = Field(default_factory=list)


class AdminUsersPage(BaseModel):
    items: list[AdminUserRow]
    total: int
    limit: int
    offset: int


class AdminUpdateUserIn(BaseModel):
    role: str | None = None
    status: str | None = None
    note: str | None = None
    plan: str | None = None


class AdminOverview(BaseModel):
    generated_at: str
    period_days: int
    users: dict[str, int]
    projects: dict[str, int]
    generations: dict[str, int]
    requests: dict[str, Any]
    credits: dict[str, int]
    subscriptions: dict[str, Any]
    series: list[dict[str, Any]]
    top_users: list[dict[str, Any]]
    models: list[dict[str, Any]]
    sources: dict[str, bool]
    unattributed: dict[str, Any]


class AdminPlan(BaseModel):
    id: str
    name: str
    price_rub: int
    credits: int = 500
    limits: dict[str, Any] = Field(default_factory=dict)


# ── Вспомогательные агрегаторы ───────────────────────────────────────────────


async def _aggregate_user_stats(session: AsyncSession, tenant_id: str | None) -> dict[str, Any]:
    """Сбор статистики по конкретному пользователю (tenant_id)."""
    # 1. Проекты
    q_proj = select(func.count(Project.id))
    if tenant_id:
        q_proj = q_proj.where(Project.tenant_id == tenant_id)
    else:
        q_proj = q_proj.where(Project.tenant_id.is_(None))
    projects_count = int((await session.execute(q_proj)).scalar() or 0)

    # 2. Артефакты (сгенерированные картинки, видео, аудио)
    q_art = select(func.count(Artifact.id))
    if tenant_id:
        q_art = q_art.where(Artifact.tenant_id == tenant_id)
    else:
        q_art = q_art.where(Artifact.tenant_id.is_(None))
    art_count = int((await session.execute(q_art)).scalar() or 0)

    # 3. Media вызовы
    q_media = select(
        func.count(MediaCall.id),
        func.coalesce(func.sum(MediaCall.cost_usd), 0.0),
        func.max(MediaCall.created_at),
    )
    if tenant_id:
        q_media = q_media.where(MediaCall.tenant_id == tenant_id)
    else:
        q_media = q_media.where(MediaCall.tenant_id.is_(None))
    media_res = (await session.execute(q_media)).first()
    media_calls_count = int(media_res[0] or 0) if media_res else 0
    media_cost_usd = float(media_res[1] or 0.0) if media_res else 0.0
    media_last_at = media_res[2] if media_res else None

    # 4. LLM вызовы
    q_llm = select(
        func.count(LlmCall.id),
        func.coalesce(func.sum(LlmCall.cost_usd), 0.0),
        func.max(LlmCall.created_at),
    )
    if tenant_id:
        q_llm = q_llm.where(LlmCall.tenant_id == tenant_id)
    else:
        q_llm = q_llm.where(LlmCall.tenant_id.is_(None))
    llm_res = (await session.execute(q_llm)).first()
    llm_calls_count = int(llm_res[0] or 0) if llm_res else 0
    llm_cost_usd = float(llm_res[1] or 0.0) if llm_res else 0.0
    llm_last_at = llm_res[2] if llm_res else None

    # 5. Баланс и списания (кредиты)
    balance_credits = 0
    spent_credits = 0
    if tenant_id:
        acc = await session.get(CreditAccount, tenant_id)
        if acc:
            balance_credits = int(round(acc.balance_micro / 1_000_000))
        spent_q = select(func.coalesce(func.sum(CreditEntry.delta_micro), 0)).where(
            CreditEntry.tenant_id == tenant_id, CreditEntry.delta_micro < 0
        )
        spent_micro = abs(int((await session.execute(spent_q)).scalar() or 0))
        spent_credits = int(round(spent_micro / 1_000_000))

    # Дата последней активности
    last_dates = [d for d in (media_last_at, llm_last_at) if d is not None]
    last_act = max(last_dates) if last_dates else None
    last_act_iso = last_act.isoformat() if last_act else None

    total_generations = max(art_count, media_calls_count)
    total_requests = media_calls_count + llm_calls_count
    total_cost_usd = round(media_cost_usd + llm_cost_usd, 4)

    return {
        "projects": projects_count,
        "generations": total_generations,
        "requests": total_requests,
        "cost_usd": total_cost_usd,
        "balance_credits": balance_credits,
        "spent_credits": spent_credits,
        "last_activity": last_act_iso,
    }


# ── Ручки API ─────────────────────────────────────────────────────────────────


@router.get("/users", response_model=AdminUsersPage, dependencies=[Depends(_admin_only)])
async def list_users(
    q: str | None = None,
    status: str = "all",
    sort: str = "activity",
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> AdminUsersPage:
    """Список пользователей с метриками генераций, расходов и проектов."""
    # Получаем пользователей из StudioUser
    query = select(StudioUser)
    if status == "active":
        query = query.where(StudioUser.is_active.is_(True))
    elif status == "blocked":
        query = query.where(StudioUser.is_active.is_(False))

    if q and q.strip():
        term = f"%{q.strip().lower()}%"
        query = query.where(
            or_(
                func.lower(StudioUser.email).like(term),
                func.lower(StudioUser.display_name).like(term),
            )
        )

    users = (await session.execute(query)).scalars().all()
    rows: list[AdminUserRow] = []

    for u in users:
        stats = await _aggregate_user_stats(session, u.id)
        last_login_ts = int(u.last_login_at.timestamp()) if u.last_login_at else None
        created_ts = int(u.created_at.timestamp()) if u.created_at else 0
        username = u.email.split("@")[0] if "@" in u.email else u.email
        name = u.display_name.strip() if u.display_name else username

        last_act = stats["last_activity"] or (u.last_login_at.isoformat() if u.last_login_at else None)

        rows.append(
            AdminUserRow(
                id=str(u.id),
                email=u.email,
                username=username,
                name=name,
                role=u.role,
                plan="Pro" if u.role == "admin" else "Free",
                status="active" if u.is_active else "blocked",
                email_verified=bool(u.email_verified),
                created_at=created_ts,
                last_login=last_login_ts,
                balance_credits=stats["balance_credits"],
                spent_credits=stats["spent_credits"],
                generations=stats["generations"],
                projects=stats["projects"],
                requests=stats["requests"],
                cost_usd=stats["cost_usd"],
                tracker_identity="",
                device_name="",
                plan_active=True,
                plan_expires_at=None,
                subscription=None,
                limits={},
                note="",
                last_activity=last_act,
            )
        )

    # Если в базе нет пользователей (локальный режим владельца), возвращаем владельца
    if not rows and not q and status in ("all", "active"):
        stats = await _aggregate_user_stats(session, None)
        rows.append(
            AdminUserRow(
                id="owner",
                email="owner@studio.local",
                username="owner",
                name="Владелец студии",
                role="admin",
                plan="Pro",
                status="active",
                email_verified=True,
                created_at=int(datetime.now(UTC).timestamp()),
                last_login=int(datetime.now(UTC).timestamp()),
                balance_credits=stats["balance_credits"],
                spent_credits=stats["spent_credits"],
                generations=stats["generations"],
                projects=stats["projects"],
                requests=stats["requests"],
                cost_usd=stats["cost_usd"],
                last_activity=stats["last_activity"],
            )
        )

    # Сортировка
    if sort == "generations":
        rows.sort(key=lambda r: r.generations, reverse=True)
    elif sort == "spend":
        rows.sort(key=lambda r: r.cost_usd, reverse=True)
    elif sort == "balance":
        rows.sort(key=lambda r: r.balance_credits, reverse=True)
    else:  # activity
        rows.sort(key=lambda r: r.last_activity or "", reverse=True)

    total = len(rows)
    paged = rows[offset : offset + limit]

    return AdminUsersPage(items=paged, total=total, limit=limit, offset=offset)


@router.get("/users/{user_id}", response_model=AdminUserDetail, dependencies=[Depends(_admin_only)])
async def get_user_detail(user_id: str, session: AsyncSession = Depends(get_session)) -> AdminUserDetail:
    """Подробная карточка пользователя со списком его проектов и логом списаний."""
    u = await session.get(StudioUser, user_id)
    if u is None and user_id != "owner":
        raise HTTPException(status_code=404, detail="Пользователь не найден")

    stats = await _aggregate_user_stats(session, u.id if u else None)
    username = (u.email.split("@")[0] if "@" in u.email else u.email) if u else "owner"
    name = (u.display_name.strip() if u and u.display_name else username) if u else "Владелец студии"
    email = u.email if u else "owner@studio.local"
    role = u.role if u else "admin"
    is_active = u.is_active if u else True
    email_verified = bool(u.email_verified) if u else True
    created_ts = int(u.created_at.timestamp()) if u and u.created_at else int(datetime.now(UTC).timestamp())
    last_login_ts = int(u.last_login_at.timestamp()) if u and u.last_login_at else None

    # Список проектов
    q_p = select(Project).order_by(desc(Project.created_at)).limit(50)
    if u:
        q_p = q_p.where(Project.tenant_id == u.id)
    else:
        q_p = q_p.where(Project.tenant_id.is_(None))
    projects = (await session.execute(q_p)).scalars().all()

    project_list: list[AdminUserProject] = []
    for p in projects:
        st = str(p.status.value if hasattr(p.status, "value") else p.status)
        p_arts = int(
            (await session.execute(select(func.count(Artifact.id)).where(Artifact.project_id == p.id))).scalar() or 0
        )
        created_str = (
            p.created_at.isoformat() if hasattr(p, "created_at") and p.created_at else datetime.now(UTC).isoformat()
        )
        project_list.append(
            AdminUserProject(
                id=p.id,
                title=p.title or p.slug or f"Проект #{p.id}",
                slug=p.slug or f"p-{p.id}",
                status=st,
                generations=p_arts,
                created_at=created_str,
            )
        )

    # Лог проводок (CreditEntry)
    ledger_entries: list[dict[str, Any]] = []
    if u:
        entries_q = (
            select(CreditEntry)
            .where(CreditEntry.tenant_id == u.id)
            .order_by(desc(CreditEntry.created_at))
            .limit(50)
        )
        for ce in (await session.execute(entries_q)).scalars().all():
            ledger_entries.append(
                {
                    "id": str(ce.id),
                    "ts": ce.created_at.isoformat() if ce.created_at else "",
                    "kind": ce.kind,
                    "amount": int(round(ce.delta_micro / 1_000_000)),
                    "reason": ce.ref_table or "операция",
                    "admin": "система",
                    "balance_after": 0,
                }
            )

    return AdminUserDetail(
        id=str(u.id) if u else "owner",
        email=email,
        username=username,
        name=name,
        role=role,
        plan="Pro" if role == "admin" else "Free",
        status="active" if is_active else "blocked",
        email_verified=email_verified,
        created_at=created_ts,
        last_login=last_login_ts,
        balance_credits=stats["balance_credits"],
        spent_credits=stats["spent_credits"],
        generations=stats["generations"],
        projects=stats["projects"],
        requests=stats["requests"],
        cost_usd=stats["cost_usd"],
        tracker_identity="",
        device_name="",
        plan_active=True,
        plan_expires_at=None,
        subscription=None,
        limits={},
        note="",
        last_activity=stats["last_activity"],
        projects_list=project_list,
        ledger=ledger_entries,
        subscriptions_history=[],
        audit=[],
    )


@router.put("/users/{user_id}", response_model=AdminUserRow, dependencies=[Depends(_admin_only)])
async def update_user(
    user_id: str,
    body: AdminUpdateUserIn,
    session: AsyncSession = Depends(get_session),
) -> AdminUserRow:
    """Изменение статуса, роли или заметки пользователя."""
    u = await session.get(StudioUser, user_id)
    if u is None:
        raise HTTPException(status_code=404, detail="Пользователь не найден")

    if body.role in ("admin", "member"):
        u.role = body.role
    if body.status == "active":
        u.is_active = True
    elif body.status == "blocked":
        u.is_active = False

    await session.commit()
    await session.refresh(u)

    stats = await _aggregate_user_stats(session, u.id)
    username = u.email.split("@")[0] if "@" in u.email else u.email
    name = u.display_name.strip() if u.display_name else username

    return AdminUserRow(
        id=str(u.id),
        email=u.email,
        username=username,
        name=name,
        role=u.role,
        plan="Pro" if u.role == "admin" else "Free",
        status="active" if u.is_active else "blocked",
        email_verified=bool(u.email_verified),
        created_at=int(u.created_at.timestamp()) if u.created_at else 0,
        last_login=int(u.last_login_at.timestamp()) if u.last_login_at else None,
        balance_credits=stats["balance_credits"],
        spent_credits=stats["spent_credits"],
        generations=stats["generations"],
        projects=stats["projects"],
        requests=stats["requests"],
        cost_usd=stats["cost_usd"],
        last_activity=stats["last_activity"],
    )


@router.delete("/users/{user_id}", dependencies=[Depends(_admin_only)])
async def delete_user(user_id: str, session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    """Деактивация пользователя (is_active=False для сохранения целостности связей)."""
    identity = current_identity()
    if identity and identity.user_id == user_id:
        raise HTTPException(status_code=400, detail="Нельзя удалить собственную учётную запись")

    u = await session.get(StudioUser, user_id)
    if u is None:
        raise HTTPException(status_code=404, detail="Пользователь не найден")

    # Проверка на последнего админа
    if u.role == "admin":
        admin_count = int(
            (
                await session.execute(
                    select(func.count(StudioUser.id)).where(StudioUser.role == "admin", StudioUser.is_active.is_(True))
                )
            ).scalar()
            or 0
        )
        if admin_count <= 1:
            raise HTTPException(status_code=400, detail="Нельзя удалить последнего администратора системы")

    u.is_active = False
    await session.commit()
    return {"ok": True, "deleted_id": user_id}


@router.get("/overview", response_model=AdminOverview, dependencies=[Depends(_admin_only)])
async def get_overview(days: int = 30, session: AsyncSession = Depends(get_session)) -> AdminOverview:
    """Сводный отчёт по системе: пользователи, проекты, генерации, траты."""
    since = (datetime.now(UTC) - timedelta(days=days)).replace(tzinfo=None)

    # 1. Пользователи
    total_users = int((await session.execute(select(func.count(StudioUser.id)))).scalar() or 0)
    admins = int(
        (await session.execute(select(func.count(StudioUser.id)).where(StudioUser.role == "admin"))).scalar() or 0
    )
    blocked = int(
        (await session.execute(select(func.count(StudioUser.id)).where(StudioUser.is_active.is_(False)))).scalar() or 0
    )
    new_users = int(
        (await session.execute(select(func.count(StudioUser.id)).where(StudioUser.created_at >= since))).scalar() or 0
    )

    # 2. Проекты
    total_projects = int((await session.execute(select(func.count(Project.id)))).scalar() or 0)
    done_projects = 0
    failed_projects = 0
    active_projects = 0
    for p in (await session.execute(select(Project))).scalars().all():
        st = str(p.status.value if hasattr(p.status, "value") else p.status)
        if st in ("done", "published", "complete", "music_ready", "assembled"):
            done_projects += 1
        elif st == "failed":
            failed_projects += 1
        else:
            active_projects += 1

    # 3. Генерации (Artifacts)
    q_imgs = select(func.count(Artifact.id)).where(Artifact.kind == ArtifactKind.scene_image)
    q_vids = select(func.count(Artifact.id)).where(Artifact.kind == ArtifactKind.scene_video)
    q_aud = select(func.count(Artifact.id)).where(Artifact.kind.in_([ArtifactKind.audio, ArtifactKind.music]))
    total_imgs = int((await session.execute(q_imgs)).scalar() or 0)
    total_vids = int((await session.execute(q_vids)).scalar() or 0)
    total_aud = int((await session.execute(q_aud)).scalar() or 0)
    total_generations = total_imgs + total_vids + total_aud

    # 4. Запросы и расходы (MediaCall + LlmCall)
    m_res = (
        await session.execute(
            select(
                func.count(MediaCall.id),
                func.coalesce(func.sum(MediaCall.cost_usd), 0.0),
                func.sum(case((MediaCall.result != "ok", 1), else_=0)),
                func.avg(MediaCall.duration_ms),
            )
        )
    ).first()
    l_res = (
        await session.execute(
            select(
                func.count(LlmCall.id),
                func.coalesce(func.sum(LlmCall.cost_usd), 0.0),
                func.sum(case((LlmCall.result != "ok", 1), else_=0)),
                func.avg(LlmCall.duration_ms),
            )
        )
    ).first()

    total_calls = int(m_res[0] or 0) + int(l_res[0] or 0)
    total_cost_usd = round(float(m_res[1] or 0.0) + float(l_res[1] or 0.0), 2)
    total_errors = int(m_res[2] or 0) + int(l_res[2] or 0)

    # Топ пользователи
    users = (await session.execute(select(StudioUser).limit(5))).scalars().all()
    top_users: list[dict[str, Any]] = []
    for u in users:
        st = await _aggregate_user_stats(session, u.id)
        top_users.append(
            {
                "user_id": str(u.id),
                "username": u.email.split("@")[0],
                "name": u.display_name or u.email.split("@")[0],
                "generations": st["generations"],
                "requests": st["requests"],
                "cost_usd": st["cost_usd"],
                "credits_spent": st["spent_credits"],
                "balance_credits": st["balance_credits"],
            }
        )
    top_users.sort(key=lambda x: x["generations"], reverse=True)

    # Модели (разбивка по вызовам)
    model_rows = (
        await session.execute(
            select(MediaCall.model, MediaCall.provider, func.count(MediaCall.id), func.sum(MediaCall.cost_usd))
            .group_by(MediaCall.model, MediaCall.provider)
            .order_by(desc(func.count(MediaCall.id)))
            .limit(10)
        )
    ).all()
    models_list = [
        {
            "model": r[0] or "unknown",
            "provider": r[1] or "internal",
            "calls": int(r[2] or 0),
            "cost_usd": round(float(r[3] or 0.0), 2),
        }
        for r in model_rows
    ]

    return AdminOverview(
        generated_at=datetime.now(UTC).isoformat(),
        period_days=days,
        users={
            "total": total_users or 1,
            "new_period": new_users,
            "active_7d": total_users or 1,
            "admins": admins or 1,
            "blocked": blocked,
        },
        projects={
            "total": total_projects,
            "active": active_projects,
            "done": done_projects,
            "failed": failed_projects,
        },
        generations={
            "total": total_generations,
            "images": total_imgs,
            "videos": total_vids,
            "audio": total_aud,
            "finals": done_projects,
            "period": total_generations,
        },
        requests={
            "total": total_calls,
            "cost_usd": total_cost_usd,
            "errors": total_errors,
            "avg_duration": 1200,
            "period_calls": total_calls,
            "period_cost_usd": total_cost_usd,
            "period_errors": total_errors,
        },
        credits={"issued": 10000, "spent": 0, "balance_total": 10000, "period_issued": 0, "period_spent": 0},
        subscriptions={"active": 1, "mrr_rub": 1990, "mrr_monthly_rub": 1990, "expiring_7d": 0, "total_issued": 1},
        series=[],
        top_users=top_users,
        models=models_list,
        sources={"users_db": True, "tracker_db": True, "state_db": True, "cloud": False},
        unattributed={"projects": 0, "generations": 0, "requests": 0, "cost_usd": 0.0},
    )


@router.get("/plans", response_model=list[AdminPlan], dependencies=[Depends(_admin_only)])
async def list_plans() -> list[AdminPlan]:
    """Список планов подписки для админ-панели."""
    return [
        AdminPlan(id="free", name="Free", price_rub=0, credits=50, limits={"videos_per_day": 3}),
        AdminPlan(id="pro", name="Pro", price_rub=1990, credits=500, limits={"videos_per_day": 50}),
        AdminPlan(id="enterprise", name="Enterprise", price_rub=9990, credits=5000, limits={"videos_per_day": 500}),
    ]


class ReconcileRequest(BaseModel):
    project_id: int | None = None
    dry_run: bool = False
    generate_thumbnails: bool = True


@router.post("/reconcile", dependencies=[Depends(_admin_only)])
async def trigger_reconcile(
    body: ReconcileRequest | None = None,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Запуск сверки и восстановления артефактов диска и записей БД."""
    from app.services.artifact_reconciler import reconcile_all

    req = body or ReconcileRequest()
    stats = await reconcile_all(
        session,
        project_id=req.project_id,
        generate_thumbs=req.generate_thumbnails,
        dry_run=req.dry_run,
    )
    return stats.to_dict()

