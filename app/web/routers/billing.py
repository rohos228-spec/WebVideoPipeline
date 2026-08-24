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
    from app.services.tenant import current_tenant
    from app.settings import settings

    tenant = current_tenant()
    if tenant is None:
        # Режим владельца: кредитов нет вовсе, владелец платит провайдерам
        # напрямую. Пустой ответ честнее нуля, выданного за остаток.
        return BalanceOut()

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
