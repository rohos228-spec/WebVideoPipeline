"""Касса вокруг шага: резерв до, списание после, возврат при падении.

Связывает котировку (`quote.py`) с леджером (`credit_ledger.py`) в том
единственном месте, где известны обе стороны: шаг вот-вот пойдёт, и вот он
кончился.

**В режиме владельца касса выключена целиком.** Нет арендатора — нет холда,
нет проводок, нет обращений к счёту. Это не заглушка на время разработки:
владелец платит провайдерам напрямую, кредитов у него нет вовсе, и списывать
их не с чего. Условие одно и проверяется первым, чтобы живой конвейер вёл
себя ровно так же, как до появления кассы.

**Факт считается по журналам, а не по оценке.** Перед шагом запоминается
верхняя граница id в `llm_calls` и `media_calls`, после — суммируются
строки выше неё. Это точнее временного окна: параллельный шаг соседнего
проекта не попадёт в чужой счёт, а перезапущенный вызов попадёт в свой.

**Журналы дописываются до подсчёта.** Учёт вызовов отбивается на занятой
базе и копит строки в памяти (`llm_ledger._pending_rows`). Посчитать деньги
раньше, чем эти строки доехали, значило бы недосчитать реальный расход —
причём тем сильнее, чем тяжелее был шаг.

**Собственная сессия.** Списание идёт отдельной транзакцией: сессия шага
могла упасть, откатиться или держать блокировку, а деньги обязаны быть
записаны в любом случае.

**Объём касса выясняет сама.** Медийная часть сметы точна ровно настолько,
насколько известно число генераций: 24 клипа по прайсу — это цент в цент, а
«шаг video» без числа кадров — справочная величина из §6.1. Требовать это
число от вызывающего значит требовать помнить, что «Видео» тарифицируется по
кадрам, «Озвучка» — по символам, а «Картинки» — по кадрам, но с другой
ставкой. Забыть можно в одном месте, а недосчитается холд у всех. Поэтому
`frames` и `voice_chars` по умолчанию берутся из базы, а параметры оставлены
для случая, когда вызывающий знает точнее (перегенерация трёх кадров из
двадцати четырёх — не двадцать четыре кадра).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from loguru import logger
from sqlalchemy import func, select

from app.db import session_scope
from app.models import Frame, LlmCall, MediaCall
from app.services.credit_ledger import open_hold, release_hold, settle_hold
from app.services.credits import format_credits
from app.services.quote import quote_step
from app.services.tenant import current_tenant


@dataclass(frozen=True)
class _Mark:
    """Граница журналов на момент старта шага."""

    llm_id: int
    media_id: int


@dataclass
class StepBill:
    """Что касса сделала с этим шагом. ``None`` у холда — режим владельца."""

    tenant_id: str | None
    hold_id: str | None
    quoted_micro: int = 0
    held_micro: int = 0
    charged_micro: int = 0
    cost_usd: float = 0.0

    @property
    def billed(self) -> bool:
        return self.hold_id is not None


@asynccontextmanager
async def step_billing(
    project: Any,
    step_code: str,
    *,
    frames: int | None = None,
    voice_chars: int | None = None,
    node_key: str = "",
) -> AsyncIterator[StepBill]:
    """Обернуть выполнение шага резервом и списанием.

    Использование::

        async with step_billing(project, "video", frames=24) as bill:
            await run_the_step(...)
        # холд закрыт: списан факт, излишек вернулся

    Падение внутри блока снимает резерв целиком — клиент не платит ни за
    одну неудачную попытку (§5.4 п.5) — и пробрасывается дальше.
    """
    tenant = current_tenant()
    if tenant is None or not step_code:
        # Нет арендатора — режим владельца. Нет кода шага — такт пришёлся на
        # статус, который шагом не является: тарифицировать нечего.
        #
        # Незнакомый прайсу код — другое дело, и он сюда доходит: смета выйдет
        # нулевой, резерв нулевым, а списание всё равно посчитается по факту
        # из журналов. То есть деньги соберутся, потеряется лишь гарантия, что
        # их хватило. Так и задумано: молча не брать хуже, чем взять с
        # перерасходом, — перерасход виден в `Settlement.overran`.
        yield StepBill(tenant_id=None, hold_id=None)
        return

    project_id = int(getattr(project, "id", 0) or 0)
    async with session_scope() as session:
        if frames is None or voice_chars is None:
            auto_frames, auto_chars = await step_volume(session, project_id, step_code)
            frames = auto_frames if frames is None else frames
            voice_chars = auto_chars if voice_chars is None else voice_chars
        estimate = await quote_step(
            project, step_code, frames=frames, voice_chars=voice_chars, session=session
        )
        hold = await open_hold(
            session,
            tenant,
            project_id=project_id,
            step_code=step_code,
            amount_micro=estimate.hold_micro,
            node_key=node_key,
        )
        hold_id = hold.id
        held = int(hold.amount_micro)

    bill = StepBill(
        tenant_id=tenant,
        hold_id=hold_id,
        quoted_micro=estimate.price_micro,
        held_micro=held,
    )
    logger.info(
        "касса: #{} шаг {} — резерв {} кр (оценка {} кр, {})",
        project_id,
        step_code,
        format_credits(held, rounding="up"),
        format_credits(estimate.price_micro, rounding="up"),
        estimate.basis,
    )

    async with session_scope() as session:
        mark = await _ledger_mark(session, project_id)

    try:
        yield bill
    except BaseException:
        async with session_scope() as session:
            await release_hold(session, hold_id, memo=f"шаг {step_code} не завершён")
        logger.info("касса: #{} шаг {} упал — резерв снят целиком", project_id, step_code)
        raise

    await _flush_ledgers()
    async with session_scope() as session:
        cost, refs = await _spent_since(session, project_id, mark)
        result = await settle_hold(
            session,
            hold_id,
            cost_usd=cost,
            ref_table="llm_calls+media_calls",
            ref_ids=refs,
            memo=f"шаг {step_code}",
        )
    bill.charged_micro = result.charged_micro
    bill.cost_usd = cost
    logger.info(
        "касса: #{} шаг {} — списано {} кр, возвращено {} кр{}",
        project_id,
        step_code,
        format_credits(result.charged_micro, rounding="up"),
        format_credits(result.returned_micro),
        ", ПЕРЕРАСХОД" if result.overran else "",
    )


#: Что именно измеряет медийную часть шага. Ключ — код шага, значение —
#: какой объём для него имеет смысл. Шага нет в таблице — медиа у него нет.
_VOLUME_BY_STEP: dict[str, str] = {
    "img": "frames",
    "video": "frames",
    "audio": "chars",
}


async def step_volume(session: Any, project_id: int, step_code: str) -> tuple[int | None, int | None]:
    """Объём шага из базы: кадров и символов закадра. `None` — неприменимо.

    Возвращается пара, а не одно число, потому что у шага бывает ровно один
    осмысленный объём, и какой именно — знает шаг, а не вызывающий.
    """
    kind = _VOLUME_BY_STEP.get(step_code)
    if kind is None:
        return (None, None)
    if kind == "frames":
        total = (
            await session.execute(select(func.count(Frame.id)).where(Frame.project_id == project_id))
        ).scalar_one()
        return (int(total or 0) or None, None)
    chars = (
        await session.execute(
            select(func.coalesce(func.sum(func.length(Frame.voiceover_text)), 0)).where(
                Frame.project_id == project_id
            )
        )
    ).scalar_one()
    return (None, int(chars or 0) or None)


async def _ledger_mark(session: Any, project_id: int) -> _Mark:
    """Верхняя граница журналов до старта шага."""
    llm_id = (
        await session.execute(
            select(func.coalesce(func.max(LlmCall.id), 0)).where(LlmCall.project_id == project_id)
        )
    ).scalar_one()
    media_id = (
        await session.execute(
            select(func.coalesce(func.max(MediaCall.id), 0)).where(MediaCall.project_id == project_id)
        )
    ).scalar_one()
    return _Mark(int(llm_id or 0), int(media_id or 0))


async def _spent_since(session: Any, project_id: int, mark: _Mark) -> tuple[float, list[int]]:
    """``(потрачено, id оплаченных вызовов)`` за время шага."""
    total = 0.0
    refs: list[int] = []
    for model, floor in ((LlmCall, mark.llm_id), (MediaCall, mark.media_id)):
        rows = (
            await session.execute(
                select(model.id, model.cost_usd).where(model.project_id == project_id, model.id > floor)
            )
        ).all()
        for row_id, cost in rows:
            total += float(cost or 0.0)
            refs.append(int(row_id))
    return (round(total, 6), refs)


async def _flush_ledgers() -> None:
    """Дописать вызовы, отбитые на занятой базе во время шага."""
    from app.services import llm_ledger, media_ledger

    for module in (llm_ledger, media_ledger):
        try:
            await module.flush_pending()
        except Exception:  # noqa: BLE001
            logger.warning("касса: дозапись журнала не удалась", exc_info=True)
