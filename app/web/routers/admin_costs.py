"""Админский дашборд расходов — `/api/admin/costs/*` (docs/cost-accounting.md).

Контракт v1 заморожен: имена полей и типы не меняются. Источники — три
журнала: `llm_calls`, `media_calls` (факт себестоимости) и `credit_entries`
(списание в микрокредитах). Деньги не смешиваются: себестоимость живёт в USD,
списание — в микрокредитах (MICRO = 1_000_000), наружу отдаётся и то, и другое.

**Кредиты в разрезах считаются по тарифу.** В `credit_entries` нет разреза
kind/model: проводка живёт на шаге и проекте. Поэтому `credits_charged` в
overview/by-* — это сколько кредитов тариф (`price_micro`: себестоимость ×
маржа, округление вверх) взял бы за эти вызовы. Фактические списания — в
`reconcile` и в `charged_linked_usd`/`charged_unlinked_usd` overview.

**`unpriced` — не ноль.** Строка без цены не притворяется нулевой
себестоимостью: она попадает в `unpriced_calls` и не входит в покрытие прайса.
У медиа признак — флаг `unpriced`; у LLM отдельного флага нет, поэтому цена
неизвестна = `cost_usd IS NULL` (ноль означает «бесплатно»). Старые строки
писали неизвестную цену нулём — их тоже считаем нерасцененными (страховка),
но показываем отдельным счётчиком `legacy_zero_calls` в overview. NULL в сумму
не превращается в 0: `sum()` такие строки пропускает, и они видны только через
`unpriced_calls`/`legacy_zero_calls`.

**`ref_table` — маркер пути списания, а не имя таблицы.** `create_generations`
в схеме не существует: это метка «списывали по оценке каталога». Джойнить по
`ref_table` нельзя — в `reconcile` это просто строка разбивки.

**Два пути списания не смешиваются.** `step_billing` списывает по факту из
журналов и кладёт id вызовов в `ref_ids`; kie/outsee-create списывают по оценке
каталога и ссылок не кладут (`ref_table="create_generations"`, `ref_ids=[]`).
Проводки без ссылок нечем проверить по фактам — они считаются отдельно
(`charged_unlinked_usd`), а «ожидаемая» себестоимость для них — `charged / margin`.

**RLS.** Числа считаются в границах арендатора запроса: явный фильтр
`tenant_id` зеркалит предикат политики (`migrations/versions/0006`). На SQLite
политик нет физически, и без зеркала тест на изоляцию был бы декорацией.
Обходить RLS из роутера нельзя: платформенная картина целиком — это роль с
BYPASSRLS или режим владельца (строки `tenant_id IS NULL`), а не SELECT без
границ; платформенный тотал в SaaS — отдельная задача про роль с BYPASSRLS.

**Период.** `from`/`to` — локальные даты `YYYY-MM-DD` в Europe/Moscow. Часовой
пояс фиксирован UTC+3: перехода на летнее время в Москве нет с 2014 года, и
`zoneinfo` здесь не нужен. В БД время хранится в UTC без tzinfo — границы
переводятся в UTC, дни группируются сдвигом +3 часа.

**Пересчёт (recount) — не здесь.** Ручка зовёт `app/services/cost_recount.py`:
тем же кодом пользуется CLI (`app/backfill_media_costs.py`), иначе цены в
отчёте зависели бы от того, кто пересчитывал.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from datetime import time as dt_time
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import and_, case, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import CreditEntry, LlmCall, MediaCall, Project
from app.services import llm_ledger, media_ledger
from app.services.cost_recount import recount_costs
from app.services.credits import MICRO, margin, micro_to_usd, price_micro
from app.services.studio_auth import current_identity
from app.services.tenant import current_tenant
from app.settings import settings
from app.web.deps import get_session

_KIND_LLM = "llm"
_LLM_PROVIDER = "relay"
_LLM_UNIT = "1m_input_tokens"
#: Москва — UTC+3 круглый год (перехода на летнее время нет с 2014).
_MSK = timezone(timedelta(hours=3), "Europe/Moscow")
_MSK_SHIFT = timedelta(hours=3)
_DEFAULT_PERIOD_DAYS = 30
#: Запасной kind для строки прайса, по которой ещё не было вызовов.
_KIND_BY_UNIT: dict[str, str] = {
    "second": "video",
    "char": "audio",
    "track": "music",
    "item": "image",
}


def _admin_only() -> None:
    """404 для не-админа — то же правило, что закрывает путь в identity.py.

    Middleware отдаёт 404 участнику до роутера; проверка здесь — вторая линия,
    чтобы ручка не открылась при правке списка разрешённых путей. Режим
    владельца (личности нет) — разрешено: это его машина.
    """
    identity = current_identity()
    if identity is not None and not identity.is_admin:
        raise HTTPException(status_code=404, detail="ручка недоступна")


router = APIRouter(prefix="/admin/costs", tags=["admin-costs"], dependencies=[Depends(_admin_only)])


# ── период и фильтры ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class _Period:
    from_date: date
    to_date: date
    start_utc: datetime
    end_utc: datetime

    @property
    def from_iso(self) -> str:
        return self.from_date.isoformat()

    @property
    def to_iso(self) -> str:
        return self.to_date.isoformat()


def _pick_date(raw: str | None, default: date, name: str) -> date:
    if raw is None or not str(raw).strip():
        return default
    try:
        return date.fromisoformat(str(raw).strip())
    except ValueError as exc:
        raise HTTPException(
            status_code=400, detail=f"{name}: ожидается дата YYYY-MM-DD, получено {raw!r}"
        ) from exc


def _period(from_raw: str | None, to_raw: str | None) -> _Period:
    """Границы периода: локальные даты Москвы → UTC, как лежит в БД."""
    today = datetime.now(_MSK).date()
    start_date = _pick_date(from_raw, today - timedelta(days=_DEFAULT_PERIOD_DAYS - 1), "from")
    end_date = _pick_date(to_raw, today, "to")
    if start_date > end_date:
        raise HTTPException(status_code=400, detail="from позже to")
    start_utc = datetime.combine(start_date, dt_time.min) - _MSK_SHIFT
    end_utc = datetime.combine(end_date, dt_time.max) - _MSK_SHIFT
    return _Period(start_date, end_date, start_utc, end_utc)


def _tenant_clause(model: Any) -> Any:
    """Зеркало предиката RLS: свой арендатор, в режиме владельца — NULL."""
    tenant = current_tenant()
    return model.tenant_id == tenant if tenant is not None else model.tenant_id.is_(None)


def _time_clause(model: Any, period: _Period) -> list[Any]:
    return [
        model.created_at >= period.start_utc,
        model.created_at <= period.end_utc,
        _tenant_clause(model),
    ]


def _media_filters(
    period: _Period,
    *,
    kind: str = "",
    provider: str = "",
    model: str = "",
    project_id: int | None = None,
) -> list[Any] | None:
    """Условия по media_calls. ``None`` — этот источник в выборку не входит."""
    if kind == _KIND_LLM:
        return None
    conds = _time_clause(MediaCall, period)
    if kind and kind != "all":
        # «audio» в отчётах — это и исторический kind «tts».
        conds.append(MediaCall.kind.in_((kind, "tts")) if kind == "audio" else MediaCall.kind == kind)
    if provider:
        conds.append(MediaCall.provider == provider)
    if model:
        conds.append(MediaCall.model == model)
    if project_id is not None:
        conds.append(MediaCall.project_id == project_id)
    return conds


def _llm_filters(
    period: _Period,
    *,
    kind: str = "",
    provider: str = "",
    model: str = "",
    project_id: int | None = None,
) -> list[Any] | None:
    """Условия по llm_calls. ``None`` — этот источник в выборку не входит."""
    if kind and kind not in ("all", _KIND_LLM):
        return None
    if provider and provider != _LLM_PROVIDER:
        return None
    conds = _time_clause(LlmCall, period)
    if model:
        conds.append(or_(LlmCall.model == model, LlmCall.served_model == model))
    if project_id is not None:
        conds.append(LlmCall.project_id == project_id)
    return conds


# ── агрегаты ─────────────────────────────────────────────────────────────────

_MEDIA_UNPRICED = case((MediaCall.unpriced.is_(True), 1), else_=0)
#: Цена неизвестна = NULL (новая конвенция; ноль = «бесплатно»).
_LLM_NULL_PRICE = LlmCall.cost_usd.is_(None)
#: Старые строки: неизвестную цену писали нулём. Считаем их нерасцененными
#: (страховка на переходный период) и отдельно показываем счётчиком.
_LLM_LEGACY_ZERO = and_(LlmCall.cost_usd.is_not(None), LlmCall.cost_usd <= 0)
_LLM_UNPRICED = case((or_(_LLM_NULL_PRICE, _LLM_LEGACY_ZERO), 1), else_=0)


def _media_agg() -> list[Any]:
    return [
        func.count(MediaCall.id).label("calls"),
        func.coalesce(func.sum(MediaCall.units), 0.0).label("units"),
        func.coalesce(func.sum(MediaCall.cost_usd), 0.0).label("actual_usd"),
        func.coalesce(func.sum(_MEDIA_UNPRICED), 0).label("unpriced_calls"),
    ]


def _llm_agg() -> list[Any]:
    tokens = func.coalesce(
        func.sum(
            func.coalesce(
                LlmCall.total_tokens,
                func.coalesce(LlmCall.prompt_tokens, 0) + func.coalesce(LlmCall.completion_tokens, 0),
            )
        ),
        0,
    )
    return [
        func.count(LlmCall.id).label("calls"),
        tokens.label("units"),
        func.coalesce(func.sum(LlmCall.cost_usd), 0.0).label("actual_usd"),
        func.coalesce(func.sum(_LLM_UNPRICED), 0).label("unpriced_calls"),
    ]


def _hit(row: Any) -> tuple[int, float, float, int]:
    """(calls, units, actual_usd, unpriced_calls) из строки агрегата."""
    return (
        int(row.calls or 0),
        float(row.units or 0.0),
        float(row.actual_usd or 0.0),
        int(row.unpriced_calls or 0),
    )


def _new_bucket() -> dict[str, Any]:
    return {"calls": 0, "units": 0.0, "actual_usd": 0.0, "unpriced_calls": 0}


def _merge(store: dict[Any, dict[str, Any]], key: Any, row: Any) -> None:
    calls, units, actual, unpriced = _hit(row)
    item = store.setdefault(key, _new_bucket())
    item["calls"] += calls
    item["units"] += units
    item["actual_usd"] += actual
    item["unpriced_calls"] += unpriced


def _kind_name(raw: str) -> str:
    """«tts» в остальных отчётах студии называется «audio» — не плодим синонимы."""
    value = (raw or "").strip().lower()
    return "audio" if value == "tts" else value


def _credits(actual_usd: float) -> tuple[int, float]:
    """Сколько микрокредитов/кредитов стоит эта себестоимость по тарифу."""
    micro = price_micro(actual_usd) if actual_usd > 0 else 0
    return micro, round(micro / MICRO, 6)


def _coverage_pct(calls: int, unpriced: int) -> float:
    if calls <= 0:
        return 0.0
    return round((calls - unpriced) * 100.0 / calls, 2)


def _usd_from_micro(micro: int) -> float:
    """Микрокредиты → USD по курсу, без маржи (себестоимость в единицах счёта)."""
    return round(float(micro_to_usd(micro, with_margin=False)), 6)


async def _llm_legacy_zero_calls(session: AsyncSession, period: _Period) -> int:
    """LLM-строки, где неизвестная цена записана нулём (старая конвенция).

    Это подмножество `unpriced_calls`: отдельный счётчик нужен, чтобы переход
    леджера на `cost_usd IS NULL` был виден в отчёте, а покрытие прайса не
    прыгало вверх на ровном месте.
    """
    stmt = select(func.count(LlmCall.id)).where(
        *_time_clause(LlmCall, period), _LLM_LEGACY_ZERO
    )
    return int((await session.execute(stmt)).scalar() or 0)


def _day_expr(column: Any) -> Any:
    """Локальный день (Europe/Moscow) из UTC-времени в БД."""
    if settings.is_postgres:
        return func.to_char(column + text("interval '3 hours'"), "YYYY-MM-DD")
    return func.date(column, "+3 hours")


@lru_cache(maxsize=512)
def _media_has_price(provider: str, model: str) -> bool:
    entry = media_ledger.price_for(provider, model)
    return entry.per_unit is not None or entry.per_call is not None


@lru_cache(maxsize=512)
def _llm_has_price(model: str) -> bool:
    in_rate, out_rate = llm_ledger.price_for(model)
    return in_rate > 0 or out_rate > 0


# ── списания из леджера ──────────────────────────────────────────────────────


@dataclass
class _ChargeBucket:
    """Проводки settle по ref_table с делением на «подтверждаемые фактом»."""

    ref_table: str
    entries: int = 0
    linked_entries: int = 0
    charged_micro: int = 0
    linked_micro: int = 0
    unlinked_micro: int = 0
    #: Себестоимость, записанная в проводках со ссылками (fact basis).
    actual_usd: float = 0.0
    #: Оценка себестоимости для проводок без ссылок: charged / margin.
    expected_unlinked_usd: float = 0.0
    unlinked_ids: list[int] = field(default_factory=list)


async def _charges(session: AsyncSession, period: _Period) -> list[_ChargeBucket]:
    """Все settle-проводки периода одним запросом, разложенные по ref_table.

    Вторая выборка не нужна: строк немного (одна на шаг), а `ref_ids` — JSON,
    и по нему всё равно надо решать в Python. Никакого N+1: один SELECT.
    """
    rows = (
        await session.execute(
            select(
                CreditEntry.ref_table,
                CreditEntry.delta_micro,
                CreditEntry.cost_usd,
                CreditEntry.margin,
                CreditEntry.ref_ids,
            ).where(CreditEntry.kind == "settle", *_time_clause(CreditEntry, period))
        )
    ).all()
    default_margin = margin()
    buckets: dict[str, _ChargeBucket] = {}
    for ref_table, delta_micro, cost_usd, row_margin, ref_ids in rows:
        table = str(ref_table or "")
        bucket = buckets.setdefault(table, _ChargeBucket(ref_table=table))
        charged = -int(delta_micro or 0)
        bucket.entries += 1
        bucket.charged_micro += charged
        if ref_ids:
            bucket.linked_entries += 1
            bucket.linked_micro += charged
            bucket.actual_usd += float(cost_usd or 0.0)
            continue
        bucket.unlinked_micro += charged
        bucket.unlinked_ids.append(bucket.entries)
        rate = Decimal(str(row_margin)) if row_margin is not None else default_margin
        if rate <= 0:
            rate = default_margin
        bucket.expected_unlinked_usd += float(micro_to_usd(charged, with_margin=False) / rate)
    return sorted(buckets.values(), key=lambda b: b.charged_micro, reverse=True)


async def _facts_total(session: AsyncSession, period: _Period) -> float:
    media = (await session.execute(select(*_media_agg()).where(*_time_clause(MediaCall, period)))).one()
    llm = (await session.execute(select(*_llm_agg()).where(*_time_clause(LlmCall, period)))).one()
    return round(float(_hit(media)[2]) + float(_hit(llm)[2]), 6)


# ── схемы ответов (рядом с роутером, как в billing.py) ───────────────────────


class OverviewTotals(BaseModel):
    actual_usd: float = 0.0
    credits_charged: float = 0.0
    credits_micro: int = 0
    calls: int = 0
    unpriced_calls: int = 0
    price_coverage_pct: float = 0.0
    #: Расширение контракта (решение ведущего): списания, подтверждаемые фактом.
    charged_linked_usd: float = 0.0
    #: Расширение: проводки без ссылок на вызовы (create_generations).
    charged_unlinked_usd: float = 0.0
    #: Расширение: старые LLM-строки, где неизвестная цена записана нулём.
    #: Входят в `unpriced_calls` (страховка), здесь — их число отдельно.
    legacy_zero_calls: int = 0


class KindRow(BaseModel):
    kind: str
    calls: int = 0
    units: float = 0.0
    actual_usd: float = 0.0
    credits_charged: float = 0.0
    unpriced_calls: int = 0


class OverviewOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    from_: str = Field(alias="from")
    to: str
    totals: OverviewTotals = Field(default_factory=OverviewTotals)
    by_kind: list[KindRow] = Field(default_factory=list)


class DayRow(BaseModel):
    day: str
    kind: str
    calls: int = 0
    actual_usd: float = 0.0
    credits_charged: float = 0.0
    unpriced_calls: int = 0


class ModelRow(BaseModel):
    kind: str
    provider: str
    model: str
    calls: int = 0
    units: float = 0.0
    unit: str = ""
    actual_usd: float = 0.0
    credits_charged: float = 0.0
    avg_usd: float = 0.0
    unpriced_calls: int = 0
    has_price: bool = False


class ProjectRow(BaseModel):
    project_id: int | None = None
    project_title: str = ""
    actual_usd: float = 0.0
    credits_charged: float = 0.0
    calls: int = 0
    unpriced_calls: int = 0


class PriceRow(BaseModel):
    kind: str
    provider: str
    model: str
    unit: str = ""
    usd_per_unit: float | None = None
    usd_per_call: float | None = None
    has_price: bool = False
    calls_30d: int = 0
    unpriced_30d: int = 0


class RecountIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    from_: str | None = Field(default=None, alias="from")
    to: str | None = None
    #: Примерка по умолчанию: запись — только явным dry_run=false.
    dry_run: bool = True
    #: "unpriced" — только строки без цены (кнопка админки); "all" — переоценка
    #: всего периода по текущему прайсу (режим CLI/владельца, только явно).
    mode: str = "unpriced"


class RecountOut(BaseModel):
    scanned: int = 0
    priced: int = 0
    still_unpriced: int = 0
    added_usd: float = 0.0
    dry_run: bool = True
    #: Каким режимом считали: "unpriced" (кнопка) или "all" (CLI/владелец).
    mode: str = "unpriced"


class RefTableRow(BaseModel):
    ref_table: str
    charged_usd: float = 0.0
    entries: int = 0
    actual_usd: float = 0.0
    #: Расширения контракта: та же разбивка по «подтверждаемости фактом».
    charged_linked_usd: float = 0.0
    charged_unlinked_usd: float = 0.0
    expected_unlinked_usd: float = 0.0


class ReconcileOut(BaseModel):
    actual_usd: float = 0.0
    charged_usd: float = 0.0
    charged_linked_usd: float = 0.0
    charged_unlinked_usd: float = 0.0
    #: Ожидаемая себестоимость проводок без ссылок: charged / margin.
    expected_unlinked_usd: float = 0.0
    #: charged − actual: положительная дельта = списано больше факта.
    delta_usd: float = 0.0
    ratio: float = 0.0
    by_ref_table: list[RefTableRow] = Field(default_factory=list)


# ── ручки ────────────────────────────────────────────────────────────────────

FromQ = Annotated[str | None, Query(alias="from", description="Дата начала, YYYY-MM-DD (Москва)")]
ToQ = Annotated[str | None, Query(description="Дата конца, YYYY-MM-DD (Москва)")]


@router.get("/overview", response_model=OverviewOut)
async def costs_overview(
    from_: FromQ = None,
    to: ToQ = None,
    session: AsyncSession = Depends(get_session),
) -> OverviewOut:
    """Общая картина периода: итоги и разбивка по видам (видео/картинки/LLM)."""
    period = _period(from_, to)
    media_conds = _time_clause(MediaCall, period)
    llm_conds = _time_clause(LlmCall, period)

    kinds: dict[str, dict[str, Any]] = {}
    for row in (
        await session.execute(
            select(MediaCall.kind.label("kind"), *_media_agg())
            .where(*media_conds)
            .group_by(MediaCall.kind)
        )
    ).all():
        _merge(kinds, _kind_name(row.kind), row)
    for row in (await session.execute(select(*_llm_agg()).where(*llm_conds))).all():
        if int(row.calls or 0):
            _merge(kinds, _KIND_LLM, row)

    by_kind = [
        KindRow(
            kind=kind,
            calls=item["calls"],
            units=round(item["units"], 4),
            actual_usd=round(item["actual_usd"], 6),
            credits_charged=_credits(item["actual_usd"])[1],
            unpriced_calls=item["unpriced_calls"],
        )
        for kind, item in sorted(kinds.items(), key=lambda kv: -kv[1]["actual_usd"])
    ]

    media_total = (await session.execute(select(*_media_agg()).where(*media_conds))).one()
    llm_total = (await session.execute(select(*_llm_agg()).where(*llm_conds))).one()
    m_calls, _m_units, m_usd, m_unpriced = _hit(media_total)
    l_calls, _l_units, l_usd, l_unpriced = _hit(llm_total)
    calls = m_calls + l_calls
    actual = m_usd + l_usd
    unpriced = m_unpriced + l_unpriced
    micro, credits = _credits(actual)

    charges = await _charges(session, period)
    legacy_zero = await _llm_legacy_zero_calls(session, period)
    totals = OverviewTotals(
        actual_usd=round(actual, 6),
        credits_charged=credits,
        credits_micro=micro,
        calls=calls,
        unpriced_calls=unpriced,
        price_coverage_pct=_coverage_pct(calls, unpriced),
        charged_linked_usd=_usd_from_micro(sum(b.linked_micro for b in charges)),
        charged_unlinked_usd=_usd_from_micro(sum(b.unlinked_micro for b in charges)),
        legacy_zero_calls=legacy_zero,
    )
    return OverviewOut.model_validate(
        {
            "from": period.from_iso,
            "to": period.to_iso,
            "totals": totals,
            "by_kind": by_kind,
        }
    )


@router.get("/by-day", response_model=list[DayRow])
async def costs_by_day(
    from_: FromQ = None,
    to: ToQ = None,
    kind: str = "",
    provider: str = "",
    model: str = "",
    project_id: int | None = None,
    session: AsyncSession = Depends(get_session),
) -> list[DayRow]:
    """Расход и кредиты по дням Москвы с разрезом по видам."""
    period = _period(from_, to)
    store: dict[tuple[str, str], dict[str, Any]] = {}

    media_conds = _media_filters(period, kind=kind, provider=provider, model=model, project_id=project_id)
    if media_conds is not None:
        day = _day_expr(MediaCall.created_at)
        rows = (
            await session.execute(
                select(day.label("day"), MediaCall.kind.label("kind"), *_media_agg())
                .where(*media_conds)
                .group_by(day, MediaCall.kind)
            )
        ).all()
        for row in rows:
            _merge(store, (str(row.day), _kind_name(row.kind)), row)

    llm_conds = _llm_filters(period, kind=kind, provider=provider, model=model, project_id=project_id)
    if llm_conds is not None:
        day = _day_expr(LlmCall.created_at)
        rows = (
            await session.execute(
                select(day.label("day"), *_llm_agg()).where(*llm_conds).group_by(day)
            )
        ).all()
        for row in rows:
            if int(row.calls or 0):
                _merge(store, (str(row.day), _KIND_LLM), row)

    out = [
        DayRow(
            day=day_name,
            kind=kind_name,
            calls=item["calls"],
            actual_usd=round(item["actual_usd"], 6),
            credits_charged=_credits(item["actual_usd"])[1],
            unpriced_calls=item["unpriced_calls"],
        )
        for (day_name, kind_name), item in sorted(store.items())
    ]
    return out


@router.get("/by-model", response_model=list[ModelRow])
async def costs_by_model(
    from_: FromQ = None,
    to: ToQ = None,
    kind: str = "",
    provider: str = "",
    project_id: int | None = None,
    session: AsyncSession = Depends(get_session),
) -> list[ModelRow]:
    """Модели за период: вызовы, единицы, деньги и есть ли у модели цена."""
    period = _period(from_, to)
    store: dict[tuple[str, str, str, str], dict[str, Any]] = {}

    media_conds = _media_filters(period, kind=kind, provider=provider, project_id=project_id)
    if media_conds is not None:
        rows = (
            await session.execute(
                select(
                    MediaCall.kind.label("kind"),
                    MediaCall.provider.label("provider"),
                    MediaCall.model.label("model"),
                    MediaCall.unit.label("unit"),
                    *_media_agg(),
                )
                .where(*media_conds)
                .group_by(MediaCall.kind, MediaCall.provider, MediaCall.model, MediaCall.unit)
            )
        ).all()
        for row in rows:
            key = (_kind_name(row.kind), str(row.provider or ""), str(row.model or ""), str(row.unit or ""))
            _merge(store, key, row)

    llm_conds = _llm_filters(period, kind=kind, provider=provider, project_id=project_id)
    if llm_conds is not None:
        model_expr = func.coalesce(func.nullif(LlmCall.served_model, ""), LlmCall.model)
        rows = (
            await session.execute(
                select(model_expr.label("model"), *_llm_agg()).where(*llm_conds).group_by(model_expr)
            )
        ).all()
        for row in rows:
            key = (_KIND_LLM, _LLM_PROVIDER, str(row.model or ""), "token")
            _merge(store, key, row)

    out: list[ModelRow] = []
    for (kind_name, prov, model, unit), item in store.items():
        calls = int(item["calls"])
        actual = round(float(item["actual_usd"]), 6)
        has_price = (
            _llm_has_price(model)
            if kind_name == _KIND_LLM
            else _media_has_price(prov, model)
        )
        out.append(
            ModelRow(
                kind=kind_name,
                provider=prov,
                model=model,
                calls=calls,
                units=round(float(item["units"]), 4),
                unit=unit,
                actual_usd=actual,
                credits_charged=_credits(actual)[1],
                avg_usd=round(actual / calls, 6) if calls else 0.0,
                unpriced_calls=int(item["unpriced_calls"]),
                has_price=has_price,
            )
        )
    out.sort(key=lambda row: row.actual_usd, reverse=True)
    return out


@router.get("/by-project", response_model=list[ProjectRow])
async def costs_by_project(
    from_: FromQ = None,
    to: ToQ = None,
    session: AsyncSession = Depends(get_session),
) -> list[ProjectRow]:
    """Расход по проектам; вызовы вне проекта — отдельной строкой."""
    period = _period(from_, to)
    store: dict[int | None, dict[str, Any]] = {}

    media_rows = (
        await session.execute(
            select(MediaCall.project_id.label("project_id"), *_media_agg())
            .where(*_time_clause(MediaCall, period))
            .group_by(MediaCall.project_id)
        )
    ).all()
    llm_rows = (
        await session.execute(
            select(LlmCall.project_id.label("project_id"), *_llm_agg())
            .where(*_time_clause(LlmCall, period))
            .group_by(LlmCall.project_id)
        )
    ).all()
    for row in list(media_rows) + list(llm_rows):
        pid = int(row.project_id) if row.project_id is not None else None
        _merge(store, pid, row)

    ids = [pid for pid in store if pid is not None]
    titles: dict[int, str] = {}
    if ids:
        for pid, title in (
            await session.execute(select(Project.id, Project.title).where(Project.id.in_(ids)))
        ).all():
            titles[int(pid)] = str(title or "")

    out = [
        ProjectRow(
            project_id=pid,
            project_title=titles.get(pid, "") if pid is not None else "вне проектов",
            actual_usd=round(item["actual_usd"], 6),
            credits_charged=_credits(item["actual_usd"])[1],
            calls=item["calls"],
            unpriced_calls=item["unpriced_calls"],
        )
        for pid, item in store.items()
        if item["calls"]
    ]
    out.sort(key=lambda row: row.actual_usd, reverse=True)
    return out


def _llm_price_catalog() -> dict[str, tuple[float, float]]:
    """Сырые id моделей LLM → (input, output) за 1M токенов.

    `llm_ledger` держит прайс с нормализованными ключами и наружу не отдаёт;
    читаем тот же файл по его пути, чтобы не заводить вторую копию данных.
    """
    path = getattr(llm_ledger, "_PRICES_PATH", None)
    if not isinstance(path, Path):
        return {}
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    models = doc.get("models") or {}
    out: dict[str, tuple[float, float]] = {}
    for model_id, spec in models.items():
        try:
            out[str(model_id)] = (float(spec["input_usd_per_m"]), float(spec["output_usd_per_m"]))
        except (KeyError, TypeError, ValueError):
            continue
    return out


@router.get("/prices", response_model=list[PriceRow])
async def costs_prices(
    kind: str = "",
    session: AsyncSession = Depends(get_session),
) -> list[PriceRow]:
    """Каталог прайса и покрытие за 30 дней: какие модели дороги, какие — нет.

    `kind` у медиа берётся из фактических вызовов, а для моделей без вызовов —
    из единицы тарификации (`second`→video, `char`→audio, `track`→music,
    `item`→image). Это подсказка для интерфейса, а не источник истины.
    """
    period = _period(None, None)
    out: list[PriceRow] = []

    observed_media: dict[tuple[str, str], dict[str, Any]] = {}
    media_rows = (
        await session.execute(
            select(
                MediaCall.provider,
                MediaCall.kind,
                MediaCall.model,
                MediaCall.unit,
                *_media_agg(),
            )
            .where(*_time_clause(MediaCall, period))
            .group_by(MediaCall.provider, MediaCall.kind, MediaCall.model, MediaCall.unit)
        )
    ).all()
    for row in media_rows:
        key = (str(row.provider or ""), str(row.model or ""))
        item = observed_media.setdefault(
            key, {"calls": 0, "unpriced": 0, "kind": "", "unit": str(row.unit or "")}
        )
        calls, _units, _actual, unpriced = _hit(row)
        item["calls"] += calls
        item["unpriced"] += unpriced
        item["kind"] = _kind_name(row.kind)

    # Публичного каталога media_ledger не отдаёт; берём тот же разобранный
    # прайс, что использует расчёт цены, а не второй его разбор.
    media_catalog = media_ledger._prices()
    seen: set[tuple[str, str]] = set()
    for catalog_key, entry in sorted(media_catalog.items()):
        provider, _, model = catalog_key.partition(":")
        if not model:
            provider, model = "", provider
        seen.add((provider, model))
        obs = observed_media.get((provider, model))
        has_price = entry.per_unit is not None or entry.per_call is not None
        out.append(
            PriceRow(
                kind=obs["kind"] if obs else _KIND_BY_UNIT.get(entry.unit, ""),
                provider=provider,
                model=model,
                unit=entry.unit,
                usd_per_unit=entry.per_unit,
                usd_per_call=entry.per_call,
                has_price=has_price,
                calls_30d=int(obs["calls"]) if obs else 0,
                unpriced_30d=int(obs["unpriced"]) if obs else 0,
            )
        )
    for (provider, model), obs in observed_media.items():
        if (provider, model) in seen:
            continue
        out.append(
            PriceRow(
                kind=obs["kind"],
                provider=provider,
                model=model,
                unit=obs["unit"] or "item",
                has_price=False,
                calls_30d=int(obs["calls"]),
                unpriced_30d=int(obs["unpriced"]),
            )
        )

    observed_llm: dict[str, dict[str, Any]] = {}
    model_expr = func.coalesce(func.nullif(LlmCall.served_model, ""), LlmCall.model)
    llm_rows = (
        await session.execute(
            select(model_expr.label("model"), *_llm_agg())
            .where(*_time_clause(LlmCall, period))
            .group_by(model_expr)
        )
    ).all()
    for row in llm_rows:
        model = str(row.model or "")
        norm = llm_ledger._norm_model(model)
        item = observed_llm.setdefault(norm, {"calls": 0, "unpriced": 0, "model": model})
        calls, _units, _actual, unpriced = _hit(row)
        item["calls"] += calls
        item["unpriced"] += unpriced
        item["model"] = model

    llm_catalog = _llm_price_catalog()
    llm_seen: set[str] = set()
    for model_id, (in_rate, out_rate) in sorted(llm_catalog.items()):
        norm = llm_ledger._norm_model(model_id)
        llm_seen.add(norm)
        obs = observed_llm.get(norm)
        out.append(
            PriceRow(
                kind=_KIND_LLM,
                provider=_LLM_PROVIDER,
                model=model_id,
                unit=_LLM_UNIT,
                usd_per_unit=in_rate,
                has_price=in_rate > 0 or out_rate > 0,
                calls_30d=int(obs["calls"]) if obs else 0,
                unpriced_30d=int(obs["unpriced"]) if obs else 0,
            )
        )
    for norm, obs in observed_llm.items():
        if norm in llm_seen:
            continue
        out.append(
            PriceRow(
                kind=_KIND_LLM,
                provider=_LLM_PROVIDER,
                model=str(obs["model"]),
                unit=_LLM_UNIT,
                has_price=False,
                calls_30d=int(obs["calls"]),
                unpriced_30d=int(obs["unpriced"]),
            )
        )

    if kind and kind != "all":
        wanted = _kind_name(kind)
        out = [row for row in out if _kind_name(row.kind) == wanted]
    out.sort(key=lambda row: (row.kind, row.provider, row.model))
    return out


@router.post("/recount", response_model=RecountOut)
async def costs_recount(body: RecountIn) -> RecountOut:
    """Пересчитать себестоимость из текущих прайсов.

    Логика живёт в `app/services/cost_recount.py` — тот же код зовёт CLI
    (`app/backfill_media_costs.py`), иначе цены в отчёте зависели бы от того,
    кто пересчитывал. Здесь только период и режим:

    * `mode="unpriced"` (по умолчанию) — трогаем строки без цены: у медиа
      `unpriced=true`, у LLM `cost_usd IS NULL` (плюс старые нули переходного
      периода). Это кнопка в админке;
    * `mode="all"` — переоценка всего периода по текущему прайсу с пересчётом
      дельты; режим CLI/владельца, в ручке — только с явным флагом.

    `dry_run=true` по умолчанию: цифры считаются, база не меняется.
    """
    if body.mode not in ("unpriced", "all"):
        raise HTTPException(status_code=400, detail="mode: ожидается unpriced или all")
    period = _period(body.from_, body.to)
    result = await recount_costs(
        since=period.start_utc,
        until=period.end_utc,
        dry_run=body.dry_run,
        mode=body.mode,
    )
    return RecountOut.model_validate(result)


@router.get("/reconcile", response_model=ReconcileOut)
async def costs_reconcile(
    from_: FromQ = None,
    to: ToQ = None,
    session: AsyncSession = Depends(get_session),
) -> ReconcileOut:
    """Факт из журналов против списаний леджера — с явной дельтой.

    Проводки делятся на подтверждаемые фактом (есть `ref_ids` — `step_billing`
    списывал по журналам) и неподтверждаемые (`ref_table="create_generations"`:
    kie/outsee списывали по оценке каталога). `ref_table` — маркер пути
    списания, а не таблица в схеме: джойнить по нему нечего, поэтому это просто
    строка разбивки. Для неподтверждаемых «ожидаемая» себестоимость —
    `charged / margin`, чтобы перекос был виден без похода в журналы.
    """
    period = _period(from_, to)
    actual_usd = await _facts_total(session, period)
    charges = await _charges(session, period)

    charged_micro = sum(bucket.charged_micro for bucket in charges)
    linked_micro = sum(bucket.linked_micro for bucket in charges)
    unlinked_micro = sum(bucket.unlinked_micro for bucket in charges)
    charged_usd = _usd_from_micro(charged_micro)
    delta = round(charged_usd - actual_usd, 6)
    ratio = round(charged_usd / actual_usd, 6) if actual_usd > 0 else 0.0

    return ReconcileOut(
        actual_usd=round(actual_usd, 6),
        charged_usd=charged_usd,
        charged_linked_usd=_usd_from_micro(linked_micro),
        charged_unlinked_usd=_usd_from_micro(unlinked_micro),
        expected_unlinked_usd=round(sum(bucket.expected_unlinked_usd for bucket in charges), 6),
        delta_usd=delta,
        ratio=ratio,
        by_ref_table=[
            RefTableRow(
                ref_table=bucket.ref_table,
                charged_usd=_usd_from_micro(bucket.charged_micro),
                entries=bucket.entries,
                actual_usd=round(bucket.actual_usd, 6),
                charged_linked_usd=_usd_from_micro(bucket.linked_micro),
                charged_unlinked_usd=_usd_from_micro(bucket.unlinked_micro),
                expected_unlinked_usd=round(bucket.expected_unlinked_usd, 6),
            )
            for bucket in charges
        ],
    )
