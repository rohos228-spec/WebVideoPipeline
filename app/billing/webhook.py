"""Вебхук оплаты: подпись (HMAC-SHA256 тела) + опц. проверка IP, идемпотентность по payment_id."""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
from decimal import Decimal, InvalidOperation

from app import settings_billing
from app.billing import ledger, subscriptions
from app.billing.db import now_iso, tx
from app.billing.plans import PERIODS, get_plan

# Официальные адреса уведомлений ЮKassa.
ALLOWED_NETS = [ipaddress.ip_network(n) for n in (
    "185.71.76.0/27", "185.71.77.0/27", "77.75.153.0/25", "77.75.156.11/32",
    "77.75.156.35/32", "77.75.154.128/25", "2a02:5180::/32",
)]


class WebhookError(Exception):
    def __init__(self, status: int, msg: str):
        super().__init__(msg)
        self.status = status


def verify_signature(body: bytes, signature: str | None) -> None:
    secret = settings_billing.load().webhook_secret
    if not secret:
        raise WebhookError(503, "webhook secret not configured")
    if not signature:
        raise WebhookError(401, "missing signature")
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature.strip().lower()):
        raise WebhookError(401, "bad signature")


def verify_ip(ip: str | None) -> None:
    if not settings_billing.load().webhook_ip_check:
        return
    try:
        addr = ipaddress.ip_address(ip or "")
    except ValueError as e:
        raise WebhookError(403, "bad ip") from e
    if not any(addr in n for n in ALLOWED_NETS):
        raise WebhookError(403, "ip not allowed")


def handle(body: bytes, signature: str | None, ip: str | None = None) -> dict:
    verify_ip(ip)
    verify_signature(body, signature)
    try:
        data = json.loads(body)
        obj = data["object"]
        pid = str(obj["id"])
        amount = Decimal(str(obj["amount"]["value"]))
        currency = obj["amount"].get("currency")
    except (ValueError, KeyError, TypeError, InvalidOperation) as e:
        raise WebhookError(400, "bad payload") from e
    event = data.get("event")
    if event != "payment.succeeded" or obj.get("status") != "succeeded":
        return {"ok": True, "ignored": event}
    if currency != "RUB":
        raise WebhookError(400, "only RUB")
    if not obj.get("test", False):
        raise WebhookError(400, "only test payments in sandbox")
    meta = obj.get("metadata") or {}
    uid = str(meta.get("user_id") or "")
    if not uid:
        raise WebhookError(400, "no user")
    purpose = meta.get("purpose") or "topup"
    credits = int(amount)  # 1 ₽ = 1 кредит
    plan = None
    period = meta.get("period") or "month"
    if purpose == "subscription":
        plan = get_plan(meta.get("plan"))
        if not plan or period not in PERIODS:
            raise WebhookError(400, "bad plan")
        credits = plan.credits(period)
    with tx() as conn:
        seen = conn.execute("SELECT status, amount_rub, user_id FROM payments WHERE provider_id=?",
                            (pid,)).fetchone()
        if seen and seen["status"] == "succeeded":
            return {"ok": True, "duplicate": True}
        if seen and (Decimal(seen["amount_rub"]) != amount or seen["user_id"] != uid):
            raise WebhookError(400, "amount or user mismatch")
        ts = now_iso()
        conn.execute(
            "INSERT INTO payments(provider_id, user_id, status, amount_rub, purpose, plan, period, test,"
            " created_at, updated_at) VALUES (?,?,?,?,?,?,?,1,?,?) ON CONFLICT(provider_id) DO UPDATE"
            " SET status=excluded.status, updated_at=excluded.updated_at",
            (pid, uid, "succeeded", f"{amount:.2f}", purpose, plan.id if plan else None,
             period if plan else None, ts, ts),
        )
        if plan:
            subscriptions.activate(conn, uid, plan, period, pid)
        ledger.post(conn, user_id=uid, type="topup", amount=credits, counter_account="system:payments",
                    idempotency_key=f"payment:{pid}", purpose=purpose, ref=pid,
                    meta={"amount_rub": f"{amount:.2f}", "plan": plan.id if plan else None,
                          "period": period if plan else None})
    return {"ok": True}
