"""ЮKassa — только SANDBOX. Боевые ключи не подключаются.

Без sandbox-ключей (или в тестах) используется MockProvider без сети.
"""

from __future__ import annotations

import base64
import json
import uuid
from dataclasses import dataclass
from decimal import Decimal

from app import settings_billing
from app.settings_billing import SELLER_INN, SELLER_NAME

API_URL = "https://api.yookassa.ru/v3/payments"


@dataclass
class CreatedPayment:
    payment_id: str
    confirmation_url: str
    amount_rub: str
    test: bool = True


def build_receipt(*, email: str | None, description: str, amount_rub: Decimal) -> dict:
    """Чек 54-ФЗ (тестовый режим). Продавец — ИП Крохмаль М. В."""
    s = settings_billing.load()
    return {
        "customer": {"email": email} if email else {},
        "tax_system_code": s.tax_system_code,
        "items": [{
            "description": description[:128],
            "quantity": "1.00",
            "amount": {"value": f"{amount_rub:.2f}", "currency": "RUB"},
            "vat_code": s.vat_code,
            "payment_mode": "full_prepayment",
            "payment_subject": "service",
        }],
        "supplier": {"name": SELLER_NAME, "inn": SELLER_INN},
    }


def _payload(amount_rub: Decimal, description: str, metadata: dict, email: str | None, idem: str) -> dict:
    s = settings_billing.load()
    return {
        "amount": {"value": f"{amount_rub:.2f}", "currency": "RUB"},
        "capture": True,
        "confirmation": {"type": "redirect", "return_url": s.return_url},
        "description": description[:128],
        "metadata": {**metadata, "idempotence_key": idem},
        "receipt": build_receipt(email=email, description=description, amount_rub=amount_rub),
    }


class MockProvider:
    """Без сети: имитирует sandbox-платёж."""

    def create_payment(self, amount_rub: Decimal, description: str, metadata: dict,
                       email: str | None = None) -> CreatedPayment:
        pid = f"mock-{uuid.uuid4().hex}"
        _payload(amount_rub, description, metadata, email, pid)  # валидируем формирование чека
        return CreatedPayment(pid, f"/billing/?payment=mock&payment_id={pid}", f"{amount_rub:.2f}")


class SandboxProvider:
    def __init__(self, shop_id: str, secret_key: str):
        if not secret_key.startswith("test_"):
            raise RuntimeError("Разрешены только тестовые ключи (test_...)")
        self.shop_id, self.secret_key = shop_id, secret_key

    def create_payment(self, amount_rub: Decimal, description: str, metadata: dict,
                       email: str | None = None) -> CreatedPayment:
        import httpx

        idem = uuid.uuid4().hex
        auth = base64.b64encode(f"{self.shop_id}:{self.secret_key}".encode()).decode()
        r = httpx.post(
            API_URL,
            content=json.dumps(_payload(amount_rub, description, metadata, email, idem)),
            headers={"Authorization": f"Basic {auth}", "Idempotence-Key": idem,
                     "Content-Type": "application/json"},
            timeout=20,
        )
        r.raise_for_status()
        j = r.json()
        if not j.get("test", False):
            raise RuntimeError("Платёж не тестовый — отклонено")
        return CreatedPayment(j["id"], j["confirmation"]["confirmation_url"], j["amount"]["value"], True)


def get_provider():
    s = settings_billing.load()
    if s.secret_key.startswith("test_") and s.shop_id and not s.test_hooks:
        return SandboxProvider(s.shop_id, s.secret_key)
    return MockProvider()
