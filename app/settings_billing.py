"""Настройки биллинга VIA. Только sandbox: боевые ключи не подключаются."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

SELLER_NAME = "ИП Крохмаль М. В."
# Реквизиты — заглушки до получения данных от владельца.
SELLER_INN = os.getenv("VIA_SELLER_INN", "000000000000")
SELLER_OGRNIP = os.getenv("VIA_SELLER_OGRNIP", "")
SELLER_ADDRESS = os.getenv("VIA_SELLER_ADDRESS", "")

CURRENCY = "RUB"
RUB_PER_CREDIT = 1  # 1 кредит = 1 ₽
USD_PER_CREDIT = 0.01  # курс операций из каталога main: $0.01 = 1 кредит
YEARLY_DISCOUNT = 0.25


@dataclass(frozen=True)
class BillingSettings:
    mode: str
    shop_id: str
    secret_key: str
    webhook_secret: str
    return_url: str
    db_path: Path
    test_hooks: bool
    webhook_ip_check: bool
    vat_code: int
    tax_system_code: int

    @property
    def sandbox(self) -> bool:
        return self.mode == "sandbox"


def _data_dir() -> Path:
    raw = os.getenv("VIA_DATA_DIR") or os.getenv("DATA_DIR") or "data"
    return Path(raw)


def load() -> BillingSettings:
    mode = (os.getenv("YOOKASSA_MODE") or "sandbox").strip().lower()
    if mode != "sandbox":
        # Защита: на этом этапе разрешён только sandbox.
        mode = "sandbox"
    db = os.getenv("VIA_BILLING_DB")
    return BillingSettings(
        mode=mode,
        shop_id=os.getenv("YOOKASSA_SHOP_ID", ""),
        secret_key=os.getenv("YOOKASSA_SECRET_KEY", ""),
        webhook_secret=os.getenv("YOOKASSA_WEBHOOK_SECRET", ""),
        return_url=os.getenv("VIA_BILLING_RETURN_URL", "http://127.0.0.1:8765/billing?payment=return"),
        db_path=Path(db) if db else _data_dir() / "billing.db",
        test_hooks=os.getenv("VIA_TEST_HOOKS", "") in ("1", "true", "yes"),
        webhook_ip_check=os.getenv("YOOKASSA_WEBHOOK_IP_CHECK", "") in ("1", "true", "yes"),
        vat_code=int(os.getenv("YOOKASSA_VAT_CODE", "1")),  # 1 = без НДС
        tax_system_code=int(os.getenv("YOOKASSA_TAX_SYSTEM", "2")),  # 2 = УСН доходы
    )
