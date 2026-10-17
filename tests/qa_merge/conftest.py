import hashlib
import hmac
import importlib
import json
import uuid

import pytest
from fastapi.testclient import TestClient

from tests.qa_merge.const import (
    APP_FACTORY, AUTH, MAIL_SEND_FUNC, YOOKASSA_WEBHOOK_SECRET,
)


def _patch_dotted(monkeypatch, dotted: str, value):
    mod_name, attr = dotted.rsplit(".", 1)
    mod = importlib.import_module(mod_name)
    monkeypatch.setattr(mod, attr, value, raising=False)


@pytest.fixture()
def outbox():
    """Перехваченные письма: [{'to':..., 'subject':..., 'body':...}]."""
    return []


@pytest.fixture()
def client(tmp_path, monkeypatch, outbox):
    """Приложение на временных БД, без сети, без реальных денег."""
    # Временные БД — ничего из рабочей копии не трогаем
    monkeypatch.setenv("VIA_ACCOUNTS_DB", str(tmp_path / "accounts.db"))
    monkeypatch.setenv("VIA_BILLING_DB", str(tmp_path / "billing.db"))
    monkeypatch.setenv("VIA_PROJECT_DB", str(tmp_path / "project.db"))
    monkeypatch.setenv("VIA_DATA_DIR", str(tmp_path))
    # Авторизация включена (ТЗ п.32: на сайте включена)
    monkeypatch.setenv("VIA_AUTH_ENABLED", "1")
    # ЮKassa — только sandbox, исходящих запросов нет
    monkeypatch.setenv("YOOKASSA_MODE", "sandbox")
    monkeypatch.setenv("YOOKASSA_SHOP_ID", "sandbox-shop")
    monkeypatch.setenv("YOOKASSA_SECRET_KEY", "sandbox-key")
    monkeypatch.setenv("YOOKASSA_WEBHOOK_SECRET", YOOKASSA_WEBHOOK_SECRET)
    # Тестовые хуки
    monkeypatch.setenv("VIA_TEST_HOOKS", "1")
    # SMTP не ходит в сеть — письма складываем в outbox
    def fake_send_mail(to, subject, body, **kw):
        outbox.append({"to": to, "subject": subject, "body": body})
    _patch_dotted(monkeypatch, MAIL_SEND_FUNC, fake_send_mail)

    mod_name, factory_name = APP_FACTORY.split(":")
    factory = getattr(importlib.import_module(mod_name), factory_name)
    app = factory()
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def _extract_token(outbox_entry) -> str:
    """Токен из письма: ищем последний URL-параметр token=... или последнюю строку."""
    import re
    m = re.search(r"token=([A-Za-z0-9._\-]+)", outbox_entry["body"])
    assert m, f"в письме нет токена: {outbox_entry}"
    return m.group(1)


@pytest.fixture()
def make_user(client, outbox):
    """Регистрация + подтверждение почты + вход. Возвращает dict с данными и заголовками."""
    def _make(email=None, password="Str0ng!pass", role=None, verified=True):
        email = email or f"u{uuid.uuid4().hex[:8]}@test.local"
        r = client.post(AUTH["register"], json={"email": email, "password": password})
        assert r.status_code in (200, 201), r.text
        if verified:
            token = _extract_token(outbox[-1])
            r = client.post(AUTH["verify"], json={"token": token})
            assert r.status_code == 200, r.text
        r = client.post(AUTH["login"], json={"email": email, "password": password})
        assert r.status_code == 200, r.text
        tok = r.json().get("access_token") or r.json().get("token")
        headers = {"Authorization": f"Bearer {tok}"} if tok else {}
        me = client.get(AUTH["me"], headers=headers).json()
        if role:  # роль назначается напрямую в БД через тестовый хук/sql — см. test_roles
            me["requested_role"] = role
        return {"email": email, "password": password, "headers": headers,
                "user_id": me.get("id") or me.get("user_id"), "me": me}
    return _make


def yk_sign(body: bytes) -> str:
    """Подпись вебхука sandbox: HMAC-SHA256 телом запроса."""
    return hmac.new(YOOKASSA_WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()


def yk_webhook_body(payment_id: str, amount_rub: str, user_id, *,
                    purpose="topup", plan=None, period=None) -> bytes:
    meta = {"user_id": str(user_id), "purpose": purpose}
    if plan:
        meta["plan"] = plan
    if period:
        meta["period"] = period
    return json.dumps({
        "event": "payment.succeeded",
        "object": {
            "id": payment_id,
            "status": "succeeded",
            "test": True,
            "amount": {"value": amount_rub, "currency": "RUB"},
            "metadata": meta,
        },
    }).encode()


def post_webhook(client, body: bytes, signature: str | None = None):
    from tests.qa_merge.const import BILLING
    sig = signature if signature is not None else yk_sign(body)
    return client.post(
        BILLING["webhook"], content=body,
        headers={"Content-Type": "application/json", "X-Webhook-Signature": sig},
    )
