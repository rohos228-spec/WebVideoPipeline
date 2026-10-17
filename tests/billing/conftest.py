import hashlib
import hmac
import json
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

SECRET = "unit-webhook-secret"


@pytest.fixture(autouse=True)
def billing_env(tmp_path, monkeypatch):
    monkeypatch.setenv("VIA_BILLING_DB", str(tmp_path / "billing.db"))
    monkeypatch.setenv("YOOKASSA_MODE", "sandbox")
    monkeypatch.setenv("YOOKASSA_SHOP_ID", "")
    monkeypatch.setenv("YOOKASSA_SECRET_KEY", "")
    monkeypatch.setenv("YOOKASSA_WEBHOOK_SECRET", SECRET)
    monkeypatch.setenv("VIA_TEST_HOOKS", "1")
    monkeypatch.setenv("VIA_AUTH_ENABLED", "1")
    from app.billing import identity, limits

    identity.set_resolver(None)
    limits.reset()
    yield
    limits.reset()


@pytest.fixture()
def client():
    from app.billing.register import register

    app = FastAPI()
    register(app)
    with TestClient(app) as c:
        yield c


def hdr(uid, verified=True):
    return {"X-VIA-User-Id": uid, "X-VIA-Verified": "1" if verified else "0"}


def sign(body: bytes) -> str:
    return hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()


def hook_body(uid, amount="100.00", pid=None, **meta):
    return json.dumps({"event": "payment.succeeded", "object": {
        "id": pid or f"p-{uuid.uuid4().hex}", "status": "succeeded", "test": True,
        "amount": {"value": amount, "currency": "RUB"},
        "metadata": {"user_id": uid, "purpose": "topup", **meta}}}).encode()


def send_hook(client, body, sig=None):
    return client.post("/api/billing/webhook/yookassa", content=body,
                       headers={"X-Webhook-Signature": sign(body) if sig is None else sig})
