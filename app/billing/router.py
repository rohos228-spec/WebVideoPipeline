"""HTTP API биллинга: /api/billing/*. Провайдеры наружу не показываются."""

from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app import settings_billing
from app.billing import ledger, subscriptions, webhook
from app.billing.db import now_iso, tx
from app.billing.identity import CurrentUser
from app.billing.plans import PERIODS, PLANS, get_plan
from app.billing.yookassa_client import get_provider

router = APIRouter(prefix="/billing", tags=["billing"])

MIN_TOPUP, MAX_TOPUP = 100, 500_000


class CheckoutIn(BaseModel):
    plan: str | None = None
    period: str = "month"
    amount: int | None = Field(default=None, description="Пакет кредитов, ₽ (1 ₽ = 1 кредит)")


@router.get("/plans")
def plans() -> dict:
    return {
        "currency": "RUB",
        "rub_per_credit": settings_billing.RUB_PER_CREDIT,
        "yearly_discount": settings_billing.YEARLY_DISCOUNT,
        "seller": settings_billing.SELLER_NAME,
        "plans": [p.to_json() for p in PLANS.values()],
        "in_development": ["payouts", "storage"],
    }


@router.get("/balance")
async def balance(user: CurrentUser) -> dict:
    return {"balance": ledger.balance(user["id"]), "currency": "credits", "rub_per_credit": 1,
            "label": "Баланс VIA"}


@router.get("/ledger")
async def get_ledger(user: CurrentUser, limit: int = 100) -> dict:
    return {"entries": ledger.entries(user["id"], min(max(limit, 1), 500))}


@router.get("/subscription")
async def subscription(user: CurrentUser) -> dict:
    return subscriptions.describe(user["id"])


@router.post("/checkout")
async def checkout(body: CheckoutIn, user: CurrentUser) -> dict:
    if not user.get("email_verified"):
        raise HTTPException(403, "Подтвердите почту перед оплатой")
    if body.plan:
        plan = get_plan(body.plan)
        if not plan or body.period not in PERIODS:
            raise HTTPException(400, "Неизвестный тариф или период")
        amount = Decimal(plan.price(body.period))
        purpose, desc = "subscription", f"VIA: тариф «{plan.name}» ({'год' if body.period == 'year' else 'месяц'})"
        meta = {"user_id": user["id"], "purpose": purpose, "plan": plan.id, "period": body.period}
    else:
        if not body.amount or not (MIN_TOPUP <= body.amount <= MAX_TOPUP):
            raise HTTPException(400, f"Сумма пополнения: от {MIN_TOPUP} до {MAX_TOPUP} ₽")
        amount = Decimal(body.amount)
        purpose, desc = "package", f"VIA: пакет {body.amount} кредитов"
        meta = {"user_id": user["id"], "purpose": purpose}
    try:
        created = get_provider().create_payment(amount, desc, meta, email=user.get("email"))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, "Платёжный сервис временно недоступен") from e
    ts = now_iso()
    with tx() as conn:
        conn.execute(
            "INSERT INTO payments(provider_id, user_id, status, amount_rub, purpose, plan, period, test,"
            " created_at, updated_at) VALUES (?,?,?,?,?,?,?,1,?,?)",
            (created.payment_id, user["id"], "pending", f"{amount:.2f}", purpose,
             meta.get("plan"), meta.get("period"), ts, ts),
        )
    return {"payment_id": created.payment_id, "confirmation_url": created.confirmation_url,
            "amount": f"{amount:.2f}", "currency": "RUB", "test": True, "mode": "sandbox"}


@router.post("/webhook/yookassa")
async def yookassa_webhook(request: Request) -> dict:
    body = await request.body()
    sig = request.headers.get("X-Webhook-Signature")
    ip = request.client.host if request.client else None
    try:
        return webhook.handle(body, sig, ip)
    except webhook.WebhookError as e:
        raise HTTPException(e.status, str(e)) from e


