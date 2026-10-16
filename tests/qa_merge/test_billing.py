"""ТЗ п.37–43: ЮKassa sandbox, кошелёк, журнал, подписки, пакеты, автовозврат."""
import uuid

from tests.qa_merge.const import BILLING, TEST_HOOKS
from tests.qa_merge.conftest import post_webhook, yk_sign, yk_webhook_body


def _balance(client, headers):
    r = client.get(BILLING["balance"], headers=headers)
    assert r.status_code == 200, r.text
    j = r.json()
    return j.get("balance") if "balance" in j else j.get("credits")


def _topup(client, user, amount="500.00"):
    body = yk_webhook_body(f"pay-{uuid.uuid4().hex}", amount, user["user_id"])
    assert post_webhook(client, body).status_code == 200


# ---------- Кошелёк и журнал ----------

def test_wallet_initial_zero(client, make_user):
    u = make_user()
    assert _balance(client, u["headers"]) == 0


def test_webhook_topup_credits_wallet_1rub_1credit(client, make_user):
    """ТЗ п.39: 1 кредит = 1 ₽."""
    u = make_user()
    _topup(client, u, "890.00")
    assert _balance(client, u["headers"]) == 890


def test_ledger_records_topup_and_is_append_only(client, make_user):
    u = make_user()
    _topup(client, u, "100.00")
    r = client.get(BILLING["ledger"], headers=u["headers"])
    assert r.status_code == 200
    entries = r.json() if isinstance(r.json(), list) else r.json()["entries"]
    assert any(e.get("type") in ("topup", "credit") for e in entries)
    # журнал только дописывается: DELETE/PUT недоступны
    assert client.delete(BILLING["ledger"], headers=u["headers"]).status_code in (404, 405)
    eid = entries[0].get("id", 1)
    assert client.put(f'{BILLING["ledger"]}/{eid}', json={}, headers=u["headers"]).status_code in (404, 405)


# ---------- Вебхук: подпись и идемпотентность ----------

def test_webhook_invalid_signature_rejected(client, make_user):
    u = make_user()
    body = yk_webhook_body("pay-bad-sig", "100.00", u["user_id"])
    r = post_webhook(client, body, signature="deadbeef" * 8)
    assert r.status_code in (400, 401, 403)
    assert _balance(client, u["headers"]) == 0


def test_webhook_missing_signature_rejected(client, make_user):
    u = make_user()
    body = yk_webhook_body("pay-no-sig", "100.00", u["user_id"])
    r = client.post(BILLING["webhook"], content=body,
                    headers={"Content-Type": "application/json"})
    assert r.status_code in (400, 401, 403)


def test_webhook_idempotent_same_payment_id(client, make_user):
    """Повтор того же payment.succeeded не удваивает баланс."""
    u = make_user()
    body = yk_webhook_body("pay-idem-1", "300.00", u["user_id"])
    assert post_webhook(client, body).status_code == 200
    assert post_webhook(client, body).status_code == 200  # повтор принимаем, но не зачисляем
    assert _balance(client, u["headers"]) == 300


def test_webhook_tampered_body_rejected(client, make_user):
    """Подпись от одного тела, контент — другой."""
    u = make_user()
    body1 = yk_webhook_body("pay-t-1", "100.00", u["user_id"])
    body2 = yk_webhook_body("pay-t-1", "100000.00", u["user_id"])
    r = post_webhook(client, body2, signature=yk_sign(body1))
    assert r.status_code in (400, 401, 403)
    assert _balance(client, u["headers"]) == 0


# ---------- Checkout и подтверждение почты ----------

def test_checkout_requires_verified_email(client, make_user):
    """ТЗ п.34: подтверждение почты обязательно перед оплатой."""
    u = make_user(verified=False)
    # вход без подтверждения может быть разрешён, но checkout — нет
    r = client.post(BILLING["checkout"], json={"plan": "start", "period": "month"},
                    headers=u["headers"])
    assert r.status_code in (401, 403), r.text


def test_checkout_sandbox_no_real_network(client, make_user):
    """В sandbox checkout возвращает confirmation_url без исходящих запросов."""
    u = make_user()
    r = client.post(BILLING["checkout"], json={"plan": "start", "period": "month"},
                    headers=u["headers"])
    assert r.status_code == 200, r.text
    j = r.json()
    assert j.get("confirmation_url") or j.get("payment_id")
    assert j.get("test") is True or j.get("mode") == "sandbox"


# ---------- Тарифы ----------

def test_plans_rub_and_yearly_discount_25(client):
    """ТЗ п.41: помесячно + год −25%, валюта RUB."""
    r = client.get(BILLING["plans"])
    assert r.status_code == 200
    plans = r.json() if isinstance(r.json(), list) else r.json()["plans"]
    assert plans, "тарифы не должны быть пустыми"
    for p in plans:
        assert p.get("currency", "RUB") == "RUB"
        month, year = p.get("price_month"), p.get("price_year")
        if month and year:
            expected = round(month * 12 * 0.75)
            assert abs(year - expected) <= 1, \
                f"{p.get('id')}: год {year} != 12×{month}×0.75 ({expected})"


def test_subscription_activation_month_and_year(client, make_user):
    u = make_user()
    for period in ("month", "year"):
        body = yk_webhook_body(f"sub-{period}-{uuid.uuid4().hex[:6]}", "1990.00",
                               u["user_id"], purpose="subscription",
                               plan="start", period=period)
        assert post_webhook(client, body).status_code == 200
        sub = client.get(BILLING["subscription"], headers=u["headers"]).json()
        assert sub.get("plan") == "start"
        assert sub.get("period") == period
        assert sub.get("active") is True


def test_package_credits_do_not_expire(client, make_user):
    """ТЗ п.42: пакеты кредитов не сгорают — у пакетных начислений нет expires_at."""
    u = make_user()
    body = yk_webhook_body(f"pack-{uuid.uuid4().hex[:6]}", "890.00",
                           u["user_id"], purpose="package")
    assert post_webhook(client, body).status_code == 200
    r = client.get(BILLING["ledger"], headers=u["headers"])
    entries = r.json() if isinstance(r.json(), list) else r.json()["entries"]
    pack = next(e for e in entries if e.get("purpose") in ("package", "topup"))
    assert not pack.get("expires_at"), "пакетные кредиты не должны иметь срока сгорания"


# ---------- Списание, недостаток средств, автовозврат ----------

def test_charge_insufficient_funds_rejected(client, make_user):
    u = make_user()  # баланс 0
    r = client.post(TEST_HOOKS["run_generation"],
                    json={"model": "video-basic", "fail": False},
                    headers=u["headers"])
    assert r.status_code in (402, 403), "генерация без кредитов должна отклоняться"
    assert _balance(client, u["headers"]) == 0


def test_successful_generation_charges_wallet(client, make_user):
    u = make_user()
    _topup(client, u, "500.00")
    r = client.post(TEST_HOOKS["run_generation"],
                    json={"model": "video-basic", "fail": False},
                    headers=u["headers"])
    assert r.status_code == 200, r.text
    assert _balance(client, u["headers"]) < 500


def test_auto_refund_on_generation_error(client, make_user):
    """ТЗ п.43: при ошибке генерации кредиты возвращаются автоматически."""
    u = make_user()
    _topup(client, u, "500.00")
    before = _balance(client, u["headers"])
    r = client.post(TEST_HOOKS["run_generation"],
                    json={"model": "video-basic", "fail": True},
                    headers=u["headers"])
    assert r.status_code in (200, 500, 502)  # ошибка генерации — не ошибка биллинга
    assert _balance(client, u["headers"]) == before, "кредиты должны вернуться полностью"
    entries = client.get(BILLING["ledger"], headers=u["headers"]).json()
    entries = entries if isinstance(entries, list) else entries["entries"]
    types = [e.get("type") for e in entries]
    assert "refund" in types and "charge" in types, "в журнале должны быть charge и refund"


def test_user_cannot_read_foreign_wallet(client, make_user):
    u1, u2 = make_user(), make_user()
    _topup(client, u1, "100.00")
    assert _balance(client, u2["headers"]) == 0
    r = client.get(BILLING["ledger"], headers=u2["headers"])
    entries = r.json() if isinstance(r.json(), list) else r.json()["entries"]
    assert entries == [] or all(str(e.get("user_id", "")) != str(u1["user_id"]) for e in entries)
