"""Кто я и сколько у меня денег. Первая ручка, которую зовёт фронт студии.

Ручка отвечает и в режиме владельца — тогда личности нет, и это честно сказано
в ответе. Фронту нужно уметь отличать «я не вошёл» от «система работает без
личностей вообще»: в первом случае надо показать форму входа, во втором —
показывать всё как есть.

**Баланс админа — не большое число, а его отсутствие.** Соблазн выдать
`10**18` микрокредитов велик: ничего не меняется в коде, всё «просто
работает». Так делать нельзя по двум причинам. Первая — арифметическая:
холд под шаг вычитается из остатка, проводки накапливаются, и однажды
сумма упирается в границу `BigInteger` посреди прогона. Вторая важнее:
огромный баланс — это ЛОЖЬ В ЛЕДЖЕРЕ. Проводки перестают сходиться с
себестоимостью, и ночная сверка `balance = Σ delta − Σ held` начинает
показывать расхождение на пустом месте, то есть перестаёт работать как
сигнал. Поэтому у админа не бесконечный баланс, а **отсутствие кассы**:
`step_billing` его не тарифицирует вовсе, а сюда уезжает флаг `unlimited`,
и интерфейс рисует по нему «∞», а не число.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.web.deps import get_session

router = APIRouter(prefix="/me", tags=["identity"])


class MeOut(BaseModel):
    """Личность и деньги одним ответом — фронт рисует шапку из него."""

    accounts_enabled: bool
    tenant_id: str | None = None
    email: str = ""
    role: str = ""
    display_name: str = ""
    #: true — кассы для этого пользователя нет; баланс показывать не нужно.
    unlimited: bool = False
    balance_micro: int = 0
    # "0", а не "0.00": `format_credits` ниже кредита показывает четыре знака
    # и обрезает хвост — одна картинка стоит 0.0105, и округлить её до 0.01
    # значит завысить цену на 90%.
    balance_credits: str = "0"


@router.get("")
async def me(request: Request, session: AsyncSession = Depends(get_session)) -> MeOut:
    from app.models import StudioUser
    from app.services.credit_ledger import balance_micro
    from app.services.credits import format_credits
    from app.settings import settings

    if not settings.accounts_enabled:
        # Режим владельца: личностей нет, кассы нет, показывать нечего и
        # прятать нечего. `unlimited` здесь честен — платит владелец напрямую.
        return MeOut(accounts_enabled=False, unlimited=True)

    identity = request.scope.get("studio_identity")
    if identity is None:
        # До сюда не доходят без токена: middleware закрывает `/api/*`. Ветка
        # оставлена не для порядка, а на случай, когда путь окажется в списке
        # публичных по недосмотру, — тогда ответ пустой, а не 500.
        return MeOut(accounts_enabled=True)

    user = await session.get(StudioUser, identity.tenant_id)
    display_name = user.display_name if user is not None else ""

    if identity.is_admin:
        return MeOut(
            accounts_enabled=True,
            tenant_id=identity.tenant_id,
            email=identity.email,
            role=identity.role,
            display_name=display_name,
            unlimited=True,
        )

    available = await balance_micro(session, identity.tenant_id)
    return MeOut(
        accounts_enabled=True,
        tenant_id=identity.tenant_id,
        email=identity.email,
        role=identity.role,
        display_name=display_name,
        unlimited=False,
        balance_micro=available,
        balance_credits=format_credits(available, rounding="down"),
    )
