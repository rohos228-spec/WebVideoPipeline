"""Цена до нажатия и состояние счёта. То, из чего чат рисует карточку шага.

Три ручки, и каждая отвечает на вопрос, который пользователь задаёт вслух:

* «сколько стоит этот шаг» — `GET /projects/{id}/steps/{code}/quote`;
* «что сгорит, если я это переделаю» — тот же путь с `?cascade=1`;
* «сколько у меня осталось» — `GET /billing/balance`.

**Почему каскад — не отдельная ручка, а флаг.** Разброс двадцатикратный:
перерисовать кадр стоит 0.68 кредита, переделать раскадровку — 16.59
(`docs/SAAS-PIVOT.md` §7.1). Это две цены одного и того же действия, и
показывать их надо рядом, а не по разным адресам: интерфейс обязан объяснить
разницу до нажатия, а не после списания.

**Деньги наружу уходят в микрокредитах и строкой сразу.** Микрокредиты —
чтобы клиент не округлял их сам и не получил 0.01 там, где 0.0105 (это
завышение на 90% на самой частой операции). Строка — чтобы правило
округления жило в одном месте: цена вверх, баланс вниз.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.web.deps import get_session

router = APIRouter(tags=["billing"])


class StepPrice(BaseModel):
    """Смета одного шага в том виде, в каком её показывают человеку."""

    step_code: str
    price_micro: int
    price_credits: str
    hold_micro: int
    hold_credits: str
    #: media — цена точна по прайсу; history — по прогонам; prior — справка.
    basis: str
    samples: int = 0
    exact: bool = False
    note: str = ""


class CascadePrice(BaseModel):
    """Радиус поражения: что именно сгорит и во сколько это обойдётся."""

    root: str
    price_micro: int
    price_credits: str
    hold_micro: int
    hold_credits: str
    exact: bool
    #: Самый дорогой шаг каскада — то, что человеку показывают первым.
    dominant: str = ""
    steps: list[StepPrice] = []


class FreeTierOut(BaseModel):
    """Состояние подарка. Пока он действует, цену показывать нечего."""

    active: bool = False
    granted_usd: float = 0.0
    cap_usd: float = 0.0
    projects: int = 0
    max_projects: int = 0
    reason: str = ""


class BalanceOut(BaseModel):
    """Остаток и последние проводки."""

    tenant_id: str | None = None
    #: true — кассы для этого пользователя нет (админ или режим владельца).
    #: Интерфейс рисует по этому флагу «∞», а не число: выдать сюда огромный
    #: остаток значило бы соврать леджеру, и ночная сверка
    #: `balance = Σ delta − Σ held` перестала бы сходиться.
    unlimited: bool = False
    balance_micro: int = 0
    balance_credits: str = "0"
    held_micro: int = 0
    free_tier: FreeTierOut = FreeTierOut()
    entries: list[dict] = []


@router.get("/projects/{project_id}/steps/video/options")
async def video_options(project_id: int, session: AsyncSession = Depends(get_session)) -> list[dict]:
    """Во что обойдётся видео при каждом доступном разрешении.

    Решение владельца: разрешение выбирается на шаге генерации, а не задаётся
    один раз на проект. Значит выбор обязан быть выбором ЦЕНЫ, а не качества
    в вакууме: 720p и 1080p отличаются вдвое по деньгам, и человек, которому
    показали только два слова, выбирает не то.

    Цена считается для каждого варианта отдельно и в тех же кредитах, что
    спишет касса, — иначе выбор делается по одной цифре, а платится другая.
    """
    from app.generation_options import VIDEO_RESOLUTIONS_BY_ID
    from app.models import Project
    from app.services.quote import quote_step

    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="проект не найден")

    frames, _ = await _cascade_volume(session, project_id)
    original = project.video_resolution
    out: list[dict] = []
    try:
        for res_id, choice in VIDEO_RESOLUTIONS_BY_ID.items():
            # Смета читает разрешение с проекта, поэтому подменяем поле в
            # памяти. В базу это не уезжает: смета — вопрос, а не выбор.
            project.video_resolution = res_id
            est = await quote_step(project, "video", frames=frames, session=session)
            out.append(
                {
                    "id": res_id,
                    "label": getattr(choice, "label", res_id),
                    "price_micro": est.price_micro,
                    "price_credits": _up(est.price_micro),
                    "exact": est.exact,
                    "current": res_id == (original or ""),
                    "note": est.note,
                }
            )
    finally:
        project.video_resolution = original
    return out


@router.get("/projects/{project_id}/steps/{step_code}/quote")
async def quote(
    project_id: int,
    step_code: str,
    *,
    cascade: bool = False,
    resolution: str = "",
    session: AsyncSession = Depends(get_session),
) -> StepPrice | CascadePrice:
    """Сколько стоит шаг — и, по флагу, весь каскад под ним.

    ``resolution`` спрашивает «а если так»: смета считается для указанного
    разрешения, но выбор проекта не меняется. Смета — вопрос, а не решение.
    """
    from app.models import Project
    from app.services.quote import quote_cascade, quote_step
    from app.services.step_billing import step_volume

    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="проект не найден")
    if resolution:
        _assert_known_resolution(resolution)
        project.video_resolution = resolution

    frames, voice_chars = await step_volume(session, project_id, step_code)
    if cascade:
        # Объём каскада считается по кадрам того же проекта: шаги внутри
        # конуса тарифицируются тем же числом генераций, что и корень.
        frames, voice_chars = await _cascade_volume(session, project_id)
        est = await quote_cascade(project, step_code, frames=frames, voice_chars=voice_chars, session=session)
        top = est.dominant()
        return CascadePrice(
            root=est.root,
            price_micro=est.price_micro,
            price_credits=_up(est.price_micro),
            hold_micro=est.hold_micro,
            hold_credits=_up(est.hold_micro),
            exact=est.exact,
            dominant=(top.step_code if top is not None else ""),
            steps=[_as_price(s) for s in est.steps if s.median_usd > 0],
        )

    return _as_price(
        await quote_step(project, step_code, frames=frames, voice_chars=voice_chars, session=session)
    )


@router.get("/projects/{project_id}/steps/quotes")
async def quotes(project_id: int, session: AsyncSession = Depends(get_session)) -> dict:
    """Цены всех шагов разом — для карточек канваса.

    Одним запросом, а не по запросу на узел: на холсте два десятка нод, и
    двадцать отдельных смет означали бы двадцать обходов истории вызовов на
    каждое открытие проекта. Здесь история читается один раз.

    Отдаётся и раскладка «тип ноды → код шага»: канвас знает про типы нод, а
    прайс — про коды шагов, и переводить одно в другое на фронте значит
    завести там вторую копию реестра, которая разойдётся с первой.
    """
    from app.models import Project
    from app.orchestrator.node_registry import NODE_TYPE_TO_STEP_CODE
    from app.orchestrator.step_dependencies import TOPO_ORDER
    from app.services.quote import quote_step

    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="проект не найден")

    frames, chars = await _cascade_volume(session, project_id)
    out: dict[str, dict] = {}
    for code in TOPO_ORDER:
        est = await quote_step(project, code, frames=frames, voice_chars=chars, session=session)
        if est.median_usd <= 0:
            # Локальные шаги (сборка, монтаж) денег не стоят. Показывать им
            # «0.00 кр» значит приучить не читать цену там, где она есть.
            continue
        out[code] = _as_price(est).model_dump()
    return {"prices": out, "by_node_type": dict(NODE_TYPE_TO_STEP_CODE)}


@router.get("/billing/balance")
async def balance(limit: int = 20, session: AsyncSession = Depends(get_session)) -> BalanceOut:
    """Остаток, сумма живых резервов и последние проводки.

    Резервы показываются отдельной строкой не для красоты: деньги под идущим
    шагом уже вычтены из остатка, и без этой строки клиент видит, что баланс
    упал, но не видит, куда.
    """
    from sqlalchemy import func, select

    from app.models import CreditEntry, CreditHold
    from app.services.credit_ledger import balance_micro
    from app.services.credits import format_credits
    from app.services.free_tier import free_tier_state
    from app.services.studio_auth import current_is_admin
    from app.services.tenant import current_tenant
    from app.settings import settings

    tenant = current_tenant()
    if tenant is None:
        # Режим владельца: кредитов нет вовсе, владелец платит провайдерам
        # напрямую. Пустой ответ честнее нуля, выданного за остаток.
        return BalanceOut(unlimited=True)
    if current_is_admin():
        # У админа не бесконечный баланс, а отсутствие кассы: `step_billing`
        # его не тарифицирует, холдов под его шагами не возникает, проводок
        # не появляется. Показывать ему ноль было бы неверно — это не «денег
        # нет», а «денег здесь не считают».
        return BalanceOut(tenant_id=tenant, unlimited=True)

    held = (
        await session.execute(
            select(func.coalesce(func.sum(CreditHold.amount_micro), 0)).where(
                CreditHold.tenant_id == tenant, CreditHold.state == "held"
            )
        )
    ).scalar_one()
    rows = (
        (
            await session.execute(
                select(CreditEntry)
                .where(CreditEntry.tenant_id == tenant)
                .order_by(CreditEntry.created_at.desc())
                .limit(max(1, min(int(limit), 200)))
            )
        )
        .scalars()
        .all()
    )
    available = await balance_micro(session, tenant)
    free = await free_tier_state(session, tenant)
    return BalanceOut(
        tenant_id=tenant,
        balance_micro=available,
        balance_credits=format_credits(available, rounding="down"),
        held_micro=int(held or 0),
        free_tier=FreeTierOut(
            active=free.active,
            granted_usd=round(free.granted_usd, 4),
            cap_usd=float(settings.free_tier_spend_cap_usd),
            projects=free.project_count,
            max_projects=int(settings.free_tier_max_projects),
            reason=free.reason,
        ),
        entries=[
            {
                "kind": r.kind,
                "delta_micro": int(r.delta_micro),
                "delta_credits": format_credits(abs(int(r.delta_micro)), rounding="down"),
                "cost_usd": float(r.cost_usd) if r.cost_usd is not None else None,
                "memo": r.memo,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ],
    )


class RedeemCouponIn(BaseModel):
    code: str


class RedeemCouponOut(BaseModel):
    ok: bool
    code: str
    credits_added: int
    balance_micro: int
    balance_credits: str
    message: str


@router.post("/billing/coupons/redeem", response_model=RedeemCouponOut)
async def redeem_coupon_endpoint(
    body: RedeemCouponIn,
    session: AsyncSession = Depends(get_session),
) -> RedeemCouponOut:
    """Активировать купон для текущего арендатора."""
    from loguru import logger

    from app.services.coupon_service import (
        CouponAlreadyRedeemedByUserError,
        CouponAlreadyUsedError,
        CouponNotFoundError,
        redeem_coupon,
    )
    from app.services.studio_auth import current_identity
    from app.services.tenant import current_tenant

    tenant = current_tenant()
    if tenant is None:
        raise HTTPException(
            status_code=400,
            detail="Активация купонов доступна только авторизованным пользователям",
        )

    ident = current_identity()
    user_id = getattr(ident, "user_id", None)

    try:
        res = await redeem_coupon(session, body.code, tenant_id=tenant, user_id=user_id)
        await session.commit()
        return RedeemCouponOut(**res)
    except CouponNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (CouponAlreadyUsedError, CouponAlreadyRedeemedByUserError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.error("ошибка активации купона {}: {}", body.code, exc)
        raise HTTPException(status_code=500, detail="Не удалось активировать купон") from exc


class CreateCouponIn(BaseModel):
    code: str | None = None
    credits: int
    max_uses: int = 1
    days: int | None = None


class DeactivateCouponIn(BaseModel):
    code: str


@router.get("/billing/admin/coupons")
async def list_coupons_endpoint(
    limit: int = 50,
    offset: int = 0,
    active_only: bool = False,
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    """Список купонов (только для администратора)."""
    from app.services.coupon_service import list_coupons
    from app.services.studio_auth import current_identity

    ident = current_identity()
    if ident is not None and not ident.is_admin:
        raise HTTPException(status_code=403, detail="Доступно только администратору студии")

    return await list_coupons(session, limit=limit, offset=offset, active_only=active_only)


@router.post("/billing/admin/coupons")
async def create_coupon_endpoint(
    body: CreateCouponIn,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Создать купон (только для администратора)."""
    import secrets
    from datetime import UTC, datetime, timedelta

    from app.services.coupon_service import create_coupon
    from app.services.studio_auth import current_identity

    ident = current_identity()
    if ident is not None and not ident.is_admin:
        raise HTTPException(status_code=403, detail="Доступно только администратору студии")

    code = body.code
    if not code:
        alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
        part1 = "".join(secrets.choice(alphabet) for _ in range(4))
        part2 = "".join(secrets.choice(alphabet) for _ in range(4))
        code = f"VP-{part1}-{part2}"

    expires_at = None
    if body.days:
        expires_at = datetime.now(UTC).replace(tzinfo=None) + timedelta(days=body.days)

    try:
        c = await create_coupon(
            session,
            code=code,
            amount_credits=body.credits,
            max_uses=body.max_uses,
            expires_at=expires_at,
        )
        await session.commit()
        return {
            "ok": True,
            "id": c.id,
            "code": c.code,
            "credits": c.amount_micro // 1_000_000,
            "max_uses": c.max_uses,
            "expires_at": c.expires_at.isoformat() if c.expires_at else None,
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/billing/admin/coupons/deactivate")
async def deactivate_coupon_endpoint(
    body: DeactivateCouponIn,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Деактивировать купон (только для администратора)."""
    from app.services.coupon_service import deactivate_coupon
    from app.services.studio_auth import current_identity

    ident = current_identity()
    if ident is not None and not ident.is_admin:
        raise HTTPException(status_code=403, detail="Доступно только администратору студии")

    ok = await deactivate_coupon(session, body.code)
    if not ok:
        raise HTTPException(status_code=404, detail="Купон не найден")
    await session.commit()
    return {"ok": True, "code": body.code.strip().upper(), "message": "Купон деактивирован"}


async def _cascade_volume(session, project_id: int) -> tuple[int | None, int | None]:
    """Кадры и символы проекта разом: каскад задевает и картинки, и озвучку."""
    from app.services.step_billing import step_volume

    frames, _ = await step_volume(session, project_id, "video")
    _, chars = await step_volume(session, project_id, "audio")
    return frames, chars


def _as_price(est) -> StepPrice:
    return StepPrice(
        step_code=est.step_code,
        price_micro=est.price_micro,
        price_credits=_up(est.price_micro),
        hold_micro=est.hold_micro,
        hold_credits=_up(est.hold_micro),
        basis=est.basis,
        samples=est.samples,
        exact=est.exact,
        note=est.note,
    )


def _assert_known_resolution(value: str) -> None:
    """Разрешение из списка, а не любая строка.

    Значение уезжает в ключ прайса; неизвестное дало бы не ошибку, а тихий
    откат к справочной цене — то есть неверную цифру, показанную как точную.
    """
    from app.generation_options import VIDEO_RESOLUTIONS_BY_ID

    if value not in VIDEO_RESOLUTIONS_BY_ID:
        raise HTTPException(
            status_code=400,
            detail=f"разрешение {value!r} неизвестно; есть: {', '.join(VIDEO_RESOLUTIONS_BY_ID)}",
        )


def _up(micro: int) -> str:
    """Цена округляется ВВЕРХ: клиент не платит меньше себестоимости с маржой."""
    from app.services.credits import format_credits

    return format_credits(micro, rounding="up")


class ModelUsageItem(BaseModel):
    model: str
    display_name: str
    kind: str  # image | video | audio | llm
    provider: str
    calls: int
    success_calls: int
    error_calls: int
    units: float
    unit_label: str
    credits_spent: str
    credits_spent_micro: int
    cost_usd: float | None = None


class KindSummaryItem(BaseModel):
    kind: str
    calls: int
    credits: str
    credits_micro: int
    cost_usd: float | None = None


class RecentUsageEntry(BaseModel):
    id: str
    created_at: str
    kind: str  # image | video | audio | llm | topup | settle | promo
    model: str
    provider: str
    description: str
    units_label: str = ""
    credits_delta: str
    credits_delta_micro: int
    cost_usd: float | None = None
    status: str
    duration_sec: int | None = None
    preview_url: str | None = None


class UsageSummary(BaseModel):
    total_spent_credits: str
    total_spent_micro: int
    total_cost_usd: float | None = None
    by_kind: dict[str, KindSummaryItem]


class UsageHistoryResponse(BaseModel):
    summary: UsageSummary
    models: list[ModelUsageItem]
    recent: list[RecentUsageEntry]
    is_admin: bool = False


def _clean_model_name(raw: str) -> tuple[str, str]:
    clean = (raw or "unknown").strip().removeprefix("kie:").removeprefix("outsee:")
    display_map = {
        "flux-2-pro": "Flux 2 Pro",
        "flux-dev": "Flux Dev",
        "alibaba-qwen-image-3": "Alibaba Qwen Image 3",
        "seedream-5-pro": "Seedream 5 Pro",
        "z-image": "Z-Image",
        "kling-3-0": "Kling 3.0 Pro",
        "veo-3-1-lite": "Veo 3.1 Lite",
        "veo-3-1-pro": "Veo 3.1 Pro",
        "gpt-image-2": "GPT Image 2",
        "gpt-image-2-vip": "GPT Image 2 VIP",
        "nano-banana-2": "Nano Banana 2",
        "nano-banana-pro": "Nano Banana Pro",
        "claude-sonnet-5": "Claude 3.5 Sonnet",
        "claude-3-5-sonnet": "Claude 3.5 Sonnet",
        "gpt-5.6-sol": "GPT-5.6 Sol",
        "gpt-4o": "GPT-4o",
        "gpt-4o-mini": "GPT-4o Mini",
        "eleven_multilingual_v2": "ElevenLabs Multilingual v2",
        "elevenlabs": "ElevenLabs",
        "suno-v4": "Suno v4",
        "minimax": "MiniMax",
        "hailuo-01": "Hailuo-01",
    }
    low = clean.lower()
    for k, v in display_map.items():
        if low == k or low == k.lower():
            return clean, v
    return clean, clean


def _unit_label(kind: str, unit: str = "") -> str:
    if kind == "image":
        return "кадров"
    if kind == "video":
        return "сек."
    if kind == "audio":
        return "симв." if unit == "char" else "треков"
    if kind == "llm":
        return "токенов"
    return unit or "шт."


@router.get("/billing/usage-history", response_model=UsageHistoryResponse)
async def usage_history(
    project_id: int | None = None,
    kind: str = "all",
    all_tenants: bool = False,
    limit: int = 100,
    offset: int = 0,
    session: AsyncSession = Depends(get_session),
) -> UsageHistoryResponse:
    """История расходов и использования всех моделей (изображения, видео, аудио, LLM)."""
    from typing import Any

    from sqlalchemy import or_, select

    from app.models import CreditEntry, LlmCall, MediaCall, Project
    from app.services.credits import MICRO, format_credits, price_micro
    from app.services.generation_storage import list_generation_files
    from app.services.studio_auth import current_is_admin
    from app.services.tenant import current_tenant

    tenant = current_tenant()
    is_admin = current_is_admin() or (tenant is None)

    if all_tenants and not is_admin:
        raise HTTPException(status_code=403, detail="Только администраторы могут просматривать общую историю")

    scope_all = bool(all_tenants and is_admin)

    # Получаем проекты арендатора для корректной фильтрации вызовов
    tenant_project_ids: list[int] = []
    if tenant is not None and not scope_all:
        p_rows = (await session.execute(select(Project.id).where(Project.tenant_id == tenant))).all()
        tenant_project_ids = [int(r[0]) for r in p_rows]

    # 1. Media calls (генерации изображений, видео, TTS)
    mc_query = select(MediaCall)
    if not scope_all and tenant is not None:
        conds = [MediaCall.tenant_id == tenant]
        if tenant_project_ids:
            conds.append(MediaCall.project_id.in_(tenant_project_ids))
        mc_query = mc_query.where(or_(*conds))
    if project_id is not None:
        mc_query = mc_query.where(MediaCall.project_id == project_id)
    mc_query = mc_query.order_by(MediaCall.created_at.desc())
    mc_rows = (await session.execute(mc_query)).scalars().all()

    # 2. LLM calls (текстовые модели)
    llm_query = select(LlmCall)
    if not scope_all and tenant is not None:
        conds = [LlmCall.tenant_id == tenant]
        if tenant_project_ids:
            conds.append(LlmCall.project_id.in_(tenant_project_ids))
        llm_query = llm_query.where(or_(*conds))
    if project_id is not None:
        llm_query = llm_query.where(LlmCall.project_id == project_id)
    llm_query = llm_query.order_by(LlmCall.created_at.desc())
    llm_rows = (await session.execute(llm_query)).scalars().all()

    # 3. Credit entries (проводки, купоны, списания)
    ce_query = select(CreditEntry)
    if not scope_all and tenant is not None:
        ce_query = ce_query.where(CreditEntry.tenant_id == tenant)
    if project_id is not None:
        ce_query = ce_query.where(CreditEntry.project_id == project_id)
    ce_query = ce_query.order_by(CreditEntry.created_at.desc())
    ce_rows = (await session.execute(ce_query)).scalars().all()

    # 4. Create workspace генерации (sidecar файлы на диске)
    create_gens: list[dict[str, Any]] = []
    if project_id is None:
        try:
            create_gens = list_generation_files(kind="all", limit=200, import_legacy=False)
        except Exception:
            create_gens = []

    # Агрегаты по моделям
    models_dict: dict[str, dict[str, Any]] = {}
    recent_entries: list[RecentUsageEntry] = []
    seen_external_ids: set[str] = set()

    for mc in mc_rows:
        if mc.external_id:
            seen_external_ids.add(str(mc.external_id))
        raw_model, display_name = _clean_model_name(mc.model or "unknown")
        m_kind = (mc.kind or "image").lower()
        if m_kind == "tts":
            m_kind = "audio"
        key = f"{m_kind}:{raw_model}"

        cost_val = float(mc.cost_usd or 0.0)
        if cost_val > 0:
            micro = price_micro(cost_val)
        elif m_kind == "image":
            micro = 500_000
        elif m_kind == "video":
            micro = int(max(1.0, float(mc.units or 1.0)) * 625_000)
        else:
            micro = 500_000

        entry = models_dict.setdefault(
            key,
            {
                "model": raw_model,
                "display_name": display_name,
                "kind": m_kind,
                "provider": mc.provider or "outsee",
                "calls": 0,
                "success_calls": 0,
                "error_calls": 0,
                "units": 0.0,
                "unit": mc.unit or "",
                "credits_spent_micro": 0,
                "cost_usd": 0.0,
            },
        )
        entry["calls"] += 1
        if mc.result == "ok":
            entry["success_calls"] += 1
        else:
            entry["error_calls"] += 1
        entry["units"] += float(mc.units or 0.0)
        entry["credits_spent_micro"] += micro
        entry["cost_usd"] += cost_val

        dur_s = int(mc.duration_ms / 1000) if mc.duration_ms else None
        units_count = round(float(mc.units or 0.0), 1)
        recent_entries.append(
            RecentUsageEntry(
                id=f"mc-{mc.id}",
                created_at=mc.created_at.isoformat() if mc.created_at else "",
                kind=m_kind,
                model=display_name,
                provider=mc.provider or "outsee",
                description=f"Генерация {m_kind} ({units_count} {_unit_label(m_kind, mc.unit)})",
                units_label=f"{units_count} {_unit_label(m_kind, mc.unit)}",
                credits_delta=f"-{format_credits(micro, rounding='up')}",
                credits_delta_micro=-micro,
                cost_usd=cost_val if is_admin else None,
                status="ok" if mc.result == "ok" else "error",
                duration_sec=dur_s,
                preview_url=None,
            )
        )

    for llm in llm_rows:
        raw_model, display_name = _clean_model_name(llm.served_model or llm.model or "unknown-llm")
        key = f"llm:{raw_model}"
        cost_val = float(llm.cost_usd or 0.0)
        micro = price_micro(cost_val) if cost_val > 0 else 0
        tokens = int(llm.total_tokens or ((llm.prompt_tokens or 0) + (llm.completion_tokens or 0)))

        entry = models_dict.setdefault(
            key,
            {
                "model": raw_model,
                "display_name": display_name,
                "kind": "llm",
                "provider": "relay",
                "calls": 0,
                "success_calls": 0,
                "error_calls": 0,
                "units": 0.0,
                "unit": "tokens",
                "credits_spent_micro": 0,
                "cost_usd": 0.0,
            },
        )
        entry["calls"] += 1
        if llm.result == "ok":
            entry["success_calls"] += 1
        else:
            entry["error_calls"] += 1
        entry["units"] += float(tokens)
        entry["credits_spent_micro"] += micro
        entry["cost_usd"] += cost_val

        dur_s = int(llm.duration_ms / 1000) if llm.duration_ms else None
        recent_entries.append(
            RecentUsageEntry(
                id=f"llm-{llm.id}",
                created_at=llm.created_at.isoformat() if llm.created_at else "",
                kind="llm",
                model=display_name,
                provider="relay",
                description=f"Текстовый запрос ({tokens:,} токенов)".replace(",", " "),
                units_label=f"{tokens:,} токенов".replace(",", " "),
                credits_delta=f"-{format_credits(micro, rounding='up')}" if micro > 0 else "0",
                credits_delta_micro=-micro,
                cost_usd=cost_val if is_admin else None,
                status="ok" if llm.result == "ok" else "error",
                duration_sec=dur_s,
                preview_url=None,
            )
        )

    for g in create_gens:
        jid = str(g.get("job_id") or "")
        gid = str(g.get("id") or "").removeprefix("gen-")
        if (jid and jid in seen_external_ids) or (gid and gid in seen_external_ids):
            continue

        raw_model, display_name = _clean_model_name(str(g.get("model") or "unknown"))
        m_kind = str(g.get("kind") or "image").lower()
        if m_kind == "audio":
            m_kind = "audio"
        key = f"{m_kind}:{raw_model}"

        quote = g.get("quote") or {}
        cr_quote = float(quote.get("credits") or 0.0)
        usd_quote = float(quote.get("usd") or 0.0)
        if cr_quote > 0:
            micro = int(cr_quote * MICRO)
        elif usd_quote > 0:
            micro = price_micro(usd_quote)
        elif m_kind == "image":
            micro = 500_000
        elif m_kind == "video":
            micro = 5_000_000
        else:
            micro = 500_000

        entry = models_dict.setdefault(
            key,
            {
                "model": raw_model,
                "display_name": display_name,
                "kind": m_kind,
                "provider": g.get("provider") or "kie",
                "calls": 0,
                "success_calls": 0,
                "error_calls": 0,
                "units": 0.0,
                "unit": "item",
                "credits_spent_micro": 0,
                "cost_usd": 0.0,
            },
        )
        entry["calls"] += 1
        is_ok = g.get("status") == "done"
        if is_ok:
            entry["success_calls"] += 1
        else:
            entry["error_calls"] += 1
        entry["units"] += 1.0
        entry["credits_spent_micro"] += micro
        entry["cost_usd"] += usd_quote

        prompt_str = str(g.get("prompt") or "")[:70]
        desc = f"Генерация {m_kind}: «{prompt_str}…»" if prompt_str else f"Генерация {m_kind} (Create)"
        recent_entries.append(
            RecentUsageEntry(
                id=f"gen-{gid or jid}",
                created_at=str(g.get("created_at") or g.get("started_at") or ""),
                kind=m_kind,
                model=display_name,
                provider=g.get("provider") or "kie",
                description=desc,
                units_label="1 кадр" if m_kind == "image" else ("1 клип" if m_kind == "video" else "1 трек"),
                credits_delta=f"-{format_credits(micro, rounding='up')}",
                credits_delta_micro=-micro,
                cost_usd=usd_quote if is_admin else None,
                status="ok" if is_ok else "error",
                duration_sec=g.get("elapsed_sec"),
                preview_url=g.get("preview_url"),
            )
        )

    # 4. Проводки купонов / пополнений / списаний
    for ce in ce_rows:
        if ce.kind == "topup":
            recent_entries.append(
                RecentUsageEntry(
                    id=f"ce-{ce.id}",
                    created_at=ce.created_at.isoformat() if ce.created_at else "",
                    kind="topup",
                    model="Купон / Пополнение",
                    provider="studio",
                    description=ce.memo or "Пополнение баланса",
                    units_label="баланс",
                    credits_delta=f"+{format_credits(int(ce.delta_micro), rounding='down')}",
                    credits_delta_micro=int(ce.delta_micro),
                    cost_usd=None,
                    status="ok",
                    duration_sec=None,
                    preview_url=None,
                )
            )
        elif ce.kind == "promo":
            recent_entries.append(
                RecentUsageEntry(
                    id=f"ce-{ce.id}",
                    created_at=ce.created_at.isoformat() if ce.created_at else "",
                    kind="promo",
                    model="Бесплатный уровень",
                    provider="studio",
                    description=ce.memo or "Бесплатный шаг конвейера",
                    units_label="промо",
                    credits_delta="0",
                    credits_delta_micro=0,
                    cost_usd=float(ce.cost_usd) if (is_admin and ce.cost_usd is not None) else None,
                    status="ok",
                    duration_sec=None,
                    preview_url=None,
                )
            )

    # Фильтрация по kind если запрошено (кроме all)
    target_kind = kind.lower().strip()
    if target_kind and target_kind != "all":
        models_filtered = [v for v in models_dict.values() if v["kind"] == target_kind]
        recent_filtered = [r for r in recent_entries if r.kind == target_kind]
    else:
        models_filtered = list(models_dict.values())
        recent_filtered = recent_entries

    # Сортировка: модели по сумме кредитов, операции по дате (свежие сверху)
    models_sorted = sorted(models_filtered, key=lambda m: m["credits_spent_micro"], reverse=True)
    recent_sorted = sorted(recent_filtered, key=lambda r: r.created_at, reverse=True)

    # Итоговые агрегаты
    by_kind_map: dict[str, KindSummaryItem] = {
        k: KindSummaryItem(kind=k, calls=0, credits="0", credits_micro=0, cost_usd=0.0 if is_admin else None)
        for k in ("image", "video", "audio", "llm")
    }

    total_spent_micro = 0
    total_cost_usd = 0.0

    for m in models_dict.values():
        k = m["kind"]
        if k in by_kind_map:
            by_kind_map[k].calls += m["calls"]
            by_kind_map[k].credits_micro += m["credits_spent_micro"]
            if is_admin:
                by_kind_map[k].cost_usd = round((by_kind_map[k].cost_usd or 0.0) + m["cost_usd"], 4)

        total_spent_micro += m["credits_spent_micro"]
        total_cost_usd += m["cost_usd"]

    for item in by_kind_map.values():
        item.credits = format_credits(item.credits_micro, rounding="up")

    models_out = [
        ModelUsageItem(
            model=m["model"],
            display_name=m["display_name"],
            kind=m["kind"],
            provider=m["provider"],
            calls=m["calls"],
            success_calls=m["success_calls"],
            error_calls=m["error_calls"],
            units=round(m["units"], 2),
            unit_label=_unit_label(m["kind"], m["unit"]),
            credits_spent=format_credits(m["credits_spent_micro"], rounding="up"),
            credits_spent_micro=m["credits_spent_micro"],
            cost_usd=round(m["cost_usd"], 4) if is_admin else None,
        )
        for m in models_sorted
    ]

    paged_recent = recent_sorted[offset : offset + max(1, min(limit, 500))]

    return UsageHistoryResponse(
        summary=UsageSummary(
            total_spent_credits=format_credits(total_spent_micro, rounding="up"),
            total_spent_micro=total_spent_micro,
            total_cost_usd=round(total_cost_usd, 4) if is_admin else None,
            by_kind=by_kind_map,
        ),
        models=models_out,
        recent=paged_recent,
        is_admin=is_admin,
    )
