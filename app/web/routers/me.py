"""Кто я и сколько у меня денег. Первая ручка, которую зовёт фронт студии.

Она же — момент заведения арендатора. Регистрации у студии нет: аккаунт
создаётся в биллинге, а здесь появляется счёт под тем же UUID при первом
запросе с валидным токеном (`app/services/tenant_provisioning.py`).

Ручка отвечает и в режиме владельца — тогда арендатора нет, и это честно
сказано в ответе. Фронту нужно уметь отличать «я не вошёл» от «система
работает без личностей вообще»: в первом случае надо вести на вход в
биллинге, во втором — показывать всё как есть.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.web.deps import get_session

router = APIRouter(prefix="/me", tags=["identity"])


class MeOut(BaseModel):
    """Личность и деньги одним ответом — фронт рисует шапку из него."""

    sso_enabled: bool
    tenant_id: str | None = None
    email: str = ""
    brand: str = ""
    balance_micro: int = 0
    # "0", а не "0.00": `format_credits` ниже кредита показывает четыре
    # знака и обрезает хвост — одна картинка стоит 0.0105, и округлить её
    # до 0.01 значит завысить цену на 90%.
    balance_credits: str = "0"
    provisioned_now: bool = False


@router.get("")
async def me(request: Request, session: AsyncSession = Depends(get_session)) -> MeOut:
    from app.services.credits import format_credits
    from app.services.tenant_provisioning import ensure_tenant
    from app.settings import settings

    if not settings.sso_enabled:
        return MeOut(sso_enabled=False)

    identity = request.scope.get("billing_identity")
    if identity is None:
        # До сюда не доходят без токена: middleware закрывает `/api/*`.
        # Ветка оставлена не для порядка, а на случай, когда путь окажется
        # в списке публичных по недосмотру, — тогда ответ пустой, а не 500.
        return MeOut(sso_enabled=True)

    result = await ensure_tenant(session, identity.tenant_id)
    await session.commit()
    return MeOut(
        sso_enabled=True,
        tenant_id=result.tenant_id,
        email=identity.email,
        brand=identity.brand,
        balance_micro=result.balance_micro,
        balance_credits=format_credits(result.balance_micro, rounding="down"),
        provisioned_now=result.created,
    )
