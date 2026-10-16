import sqlite3

import pytest

from app.billing import charge_hook, ledger, limits, prices
from app.billing.plans import PLANS
from tests.billing.conftest import hdr, hook_body, send_hook, sign


def bal(client, uid):
    return client.get("/api/billing/balance", headers=hdr(uid)).json()["balance"]


def test_models_idempotency_key_unique():
    e1 = ledger.credit("u1", 50, idempotency_key="k1")
    e2 = ledger.credit("u1", 50, idempotency_key="k1")
    assert e1.created and not e2.created and ledger.balance("u1") == 50
    assert ledger.check_integrity()


def test_models_ledger_append_only():
    ledger.credit("u1", 10, idempotency_key="k2")
    from app.billing.db import connect

    with connect() as conn, pytest.raises(sqlite3.IntegrityError):
        conn.execute("DELETE FROM ledger_txns")


def test_plans_prices_and_yearly_discount(client):
    j = client.get("/api/billing/plans").json()
    assert j["yearly_discount"] == 0.25 and j["currency"] == "RUB"
    by = {p["id"]: p for p in j["plans"]}
    assert [by[k]["price_month"] for k in ("probe", "start", "pro")] == [890, 1990, 4990]
    assert [by[k]["credits_month"] for k in ("probe", "start", "pro")] == [500, 2000, 5100]
    assert by["workshop"]["price_month"] == 9900 and by["conveyor"]["credits_month"] == 160000
    for p in j["plans"]:
        assert p["price_year"] == round(p["price_month"] * 12 * 0.75)
        assert p["streams"]["images"] == p["streams"]["video"]
        assert p["credits_expire"] is False
    assert by["conveyor"]["own_api"] and not by["factory"]["own_api"]
    assert by["factory"]["own_mechanics"] and by["factory"]["priority_support"]
    assert not by["manufactory"]["priority_support"]


def test_webhook_idempotent(client):
    body = hook_body("u2", "300.00", pid="same")
    assert send_hook(client, body).status_code == 200
    assert send_hook(client, body).status_code == 200
    assert bal(client, "u2") == 300


@pytest.mark.parametrize("sig", ["", "00" * 32])
def test_webhook_bad_signature(client, sig):
    r = send_hook(client, hook_body("u3"), sig=sig)
    assert r.status_code == 401 and bal(client, "u3") == 0


def test_webhook_tampered(client):
    b1, b2 = hook_body("u3", pid="t"), hook_body("u3", "99999.00", pid="t")
    assert send_hook(client, b2, sig=sign(b1)).status_code == 401


def test_webhook_rejects_non_test_payment(client):
    body = hook_body("u3").replace(b'"test": true', b'"test": false')
    assert send_hook(client, body).status_code == 400


def test_yookassa_checkout_sandbox_and_receipt(client):
    r = client.post("/api/billing/checkout", json={"plan": "start", "period": "year"}, headers=hdr("u4"))
    j = r.json()
    assert r.status_code == 200 and j["test"] is True and j["mode"] == "sandbox"
    assert j["amount"] == f"{round(1990 * 12 * 0.75)}.00"
    from decimal import Decimal

    from app.billing.yookassa_client import build_receipt

    rc = build_receipt(email="a@b.c", description="x", amount_rub=Decimal(100))
    assert rc["supplier"]["name"] == "ИП Крохмаль М. В." and rc["items"][0]["amount"]["currency"] == "RUB"


def test_yookassa_webhook_amount_must_match_checkout(client):
    pid = client.post("/api/billing/checkout", json={"amount": 500}, headers=hdr("u5")).json()["payment_id"]
    assert send_hook(client, hook_body("u5", "50000.00", pid=pid, purpose="package")).status_code == 400
    assert send_hook(client, hook_body("u5", "500.00", pid=pid, purpose="package")).status_code == 200
    assert bal(client, "u5") == 500


def test_yookassa_live_keys_refused(monkeypatch):
    from app.billing.yookassa_client import SandboxProvider

    with pytest.raises(RuntimeError):
        SandboxProvider("1", "live_abc")


def test_checkout_requires_verified_email(client):
    r = client.post("/api/billing/checkout", json={"amount": 500}, headers=hdr("u6", verified=False))
    assert r.status_code == 403


def test_subscription_grants_credits_and_limits(client):
    body = hook_body("u7", "1990.00", purpose="subscription", plan="start", period="month")
    assert send_hook(client, body).status_code == 200
    sub = client.get("/api/billing/subscription", headers=hdr("u7")).json()
    assert sub["active"] and sub["plan"] == "start" and sub["thread_limit"] == 2
    assert bal(client, "u7") == 2000


def test_charge_success_and_refund_on_fail(client):
    ledger.credit("u8", 100, idempotency_key="seed")
    price = prices.operation_price("video-basic")
    assert client.post("/api/_test/generation/run", json={"fail": False}, headers=hdr("u8")).status_code == 200
    assert bal(client, "u8") == 100 - price
    r = client.post("/api/_test/generation/run", json={"fail": True}, headers=hdr("u8"))
    assert r.status_code == 502 and bal(client, "u8") == 100 - price
    types = [e["type"] for e in client.get("/api/billing/ledger", headers=hdr("u8")).json()["entries"]]
    assert "refund" in types and types.count("charge") == 2
    assert ledger.check_integrity()


def test_charge_insufficient_funds(client):
    r = client.post("/api/_test/generation/run", json={}, headers=hdr("u9"))
    assert r.status_code == 402 and bal(client, "u9") == 0


def test_charge_hook_idempotent_by_job():
    ledger.credit("u10", 100, idempotency_key="s")
    charge_hook.reserve("u10", "job1", amount=30)
    charge_hook.reserve("u10", "job1", amount=30)
    assert ledger.balance("u10") == 70
    assert charge_hook.refund("job1") and not charge_hook.refund("job1")
    assert not charge_hook.settle("job1")
    assert ledger.balance("u10") == 100


def test_prices_from_main_catalog():
    # $0.01 = 1 кредит; цены как в UI main
    from app.services.vibecode_catalog import find_model

    m = find_model("kling-3-0")
    if m and m.get("pricing", {}).get("usd_per_video"):
        import math

        assert prices.operation_price("kling-3-0", "video") == math.ceil(round(m["pricing"]["usd_per_video"] * 100, 6))


def test_limits_hard_excess_queued(client):
    send_hook(client, hook_body("u11", "1990.00", purpose="subscription", plan="start", period="month"))
    lim = limits.limit_for("u11", "video")
    st = [limits.try_acquire("u11", "video", f"j{i}") for i in range(lim + 3)]
    assert st.count("running") == lim and st.count("queued") == 3
    assert limits.release("u11", "video", "j0") == f"j{lim}"
    s = limits.state("u11", "video")
    assert s["running"] == lim and s["waiting"] == 2


def test_limits_free_and_member_role(client):
    assert limits.limit_for("nobody", "video") == 1
    send_hook(client, hook_body("boss", "89000.00", purpose="subscription", plan="factory", period="month"))
    assert limits.limit_for("m1", "llm", role="member", team_owner_id="boss") == PLANS["factory"].streams.llm


def test_ledger_foreign_isolated(client):
    ledger.credit("a", 10, idempotency_key="a1")
    assert client.get("/api/billing/ledger", headers=hdr("b")).json()["entries"] == []
    assert client.delete("/api/billing/ledger", headers=hdr("a")).status_code == 405


def test_no_provider_names_in_api(client):
    import re

    text = client.get("/api/billing/plans").text + client.post(
        "/api/billing/checkout", json={"amount": 500}, headers=hdr("u12")).text
    assert not re.search(r"(?i)\b(52pay|kie|outsee|vibecode|wavespeed|elevenlabs)\b", text)
