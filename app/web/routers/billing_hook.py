"""Мост от кассы биллинга к леджеру студии: приём пополнения.

**Дыра, которую он закрывает.** Платёж принимает биллинг: YooKassa, чеки по
422-ФЗ, проводка в рублях. Кредиты, которыми платят за шаги, лежат в
`credit_accounts` студии. Между этими двумя фактами до сих пор не было
ничего: клиент мог заплатить, и баланс в студии остался бы нулевым.

**Расхождение в спеке, которое стоит назвать.** §3.3 говорит «деньги знает
только биллинг, video-pipeline не хранит балансов», а §5.3 кладёт леджер
сюда — и реализован он здесь. Спорить об этом задним числом смысла нет:
леджер уже привязан к холдам шагов, к промо-проводкам бесплатного уровня и к
себестоимости из журналов вызовов, и переносить его в биллинг значит тянуть
туда всё это. Граница проходит иначе, чем в §3.3: **рубли, чеки и платёжный
провайдер — биллинг; кредиты, холды и себестоимость — студия**, а между ними
одно направление данных, вот это.

**Секрет отдельный от JWT арендатора.** Токен клиента подписан тем же
`JWT_SECRET`, и принимать пополнение по нему значило бы позволить клиенту
пополнить себе баланс собственным токеном. Здесь общий секрет
сервер-сервер (`BILLING_WEBHOOK_SECRET`), и ручка вынесена из списка,
открытого арендаторам.

**Секрет обязан быть ASCII.** Он едет в HTTP-заголовке, а заголовки —
латиница: кириллический секрет не отправится вовсе, и выяснится это на
первом же живом платеже. Требование в валидации не поставишь (секрет
приходит из окружения), поэтому оно названо здесь и закреплено тестом.

**Идемпотентность обязательна.** Платёжные провайдеры повторяют вебхуки:
сеть моргнула, ответ не дошёл — приходит второй раз. Пополнение, начисленное
дважды, это подарок за счёт платформы, который никто не заметит до сверки.
Признаком служит `payment_id` в `memo` проводки: одна проводка на один
платёж, второй вызов возвращает тот же баланс и ничего не пишет.
"""

from __future__ import annotations

import hmac

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.web.deps import get_session

router = APIRouter(prefix="/billing-hook", tags=["billing"])

#: Метка платежа в проводке. По ней же проверяется «уже начисляли».
PAYMENT_MEMO = "платёж"


class TopUp(BaseModel):
    """Что присылает биллинг после успешного платежа."""

    tenant_id: str = Field(description="User.id биллинга, он же арендатор студии")
    #: Сумма В КРЕДИТАХ. Конвертацию рублей в доллары делает биллинг в момент
    #: платежа и фиксирует курс у себя в проводке (§5.6) — здесь курс уже не
    #: нужен и знать его студии незачем.
    credits: float = Field(gt=0)
    payment_id: str = Field(min_length=1, max_length=120)
    memo: str = ""


class TopUpResult(BaseModel):
    tenant_id: str
    balance_micro: int
    balance_credits: str
    #: false — этот платёж уже начисляли, повтор ничего не изменил.
    applied: bool


def _authorized(secret_header: str | None) -> bool:
    from app.settings import settings

    expected = settings.billing_webhook_secret.strip()
    if not expected:
        return False
    # Сравнение постоянного времени: обычное «==» на секрете утекает по
    # времени ответа, и это ровно тот случай, когда утечка бесплатна для
    # атакующего.
    return hmac.compare_digest(str(secret_header or ""), expected)


@router.post("/topup", response_model=TopUpResult)
async def topup(
    body: TopUp,
    x_billing_secret: str | None = Header(default=None, alias="X-Billing-Secret"),
    session: AsyncSession = Depends(get_session),
) -> TopUpResult:
    """Начислить кредиты после успешного платежа. Повтор безопасен."""
    from sqlalchemy import func, select

    from app.models import CreditEntry
    from app.services import credit_ledger as cl
    from app.services.credits import format_credits, price_micro
    from app.services.tenant import _normalize, tenant_scope

    if not _authorized(x_billing_secret):
        raise HTTPException(status_code=401, detail="нет доступа")

    try:
        tenant = _normalize(body.tenant_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"tenant_id не UUID: {body.tenant_id!r}") from exc
    if tenant is None:
        raise HTTPException(status_code=400, detail="нужен tenant_id")

    memo = f"{PAYMENT_MEMO} {body.payment_id}" + (f" — {body.memo}" if body.memo else "")

    # Арендатор назначается явно: ручку зовёт сервер, а не клиент, и под
    # политикой RLS запись без арендатора ушла бы в пространство владельца.
    with tenant_scope(tenant):
        already = (
            await session.execute(
                select(func.count())
                .select_from(CreditEntry)
                .where(
                    CreditEntry.tenant_id == tenant,
                    CreditEntry.kind == "topup",
                    CreditEntry.memo.like(f"{PAYMENT_MEMO} {body.payment_id}%"),
                )
            )
        ).scalar_one()
        if already:
            balance = await cl.balance_micro(session, tenant)
            return TopUpResult(
                tenant_id=tenant,
                balance_micro=balance,
                balance_credits=format_credits(balance, rounding="down"),
                applied=False,
            )

        # Кредиты приходят как цена, а не как себестоимость: маржа уже учтена
        # в том, сколько рублей человек заплатил за кредит.
        amount = price_micro(body.credits, with_margin=False)
        if amount <= 0:
            raise HTTPException(status_code=400, detail="сумма меньше одного микрокредита")
        balance = await cl.topup(session, tenant, amount, memo=memo)
        await session.commit()

    return TopUpResult(
        tenant_id=tenant,
        balance_micro=balance,
        balance_credits=format_credits(balance, rounding="down"),
        applied=True,
    )
