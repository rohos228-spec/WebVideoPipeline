import hashlib
import hmac
import time


def test_oauth_disabled_returns_503(client, user):
    assert client.get("/api/auth/oauth/telegram/start").status_code == 503
    u = user()
    assert client.post("/api/auth/oauth/google/link", json={"id_token": "x"}, headers=u["h"]).status_code == 503


def _tg_payload(bot_token, uid=4242):
    data = {"id": uid, "first_name": "Ivan", "username": "ivan", "auth_date": int(time.time())}
    check = "\n".join(f"{k}={data[k]}" for k in sorted(data))
    data["hash"] = hmac.new(hashlib.sha256(bot_token.encode()).digest(), check.encode(), hashlib.sha256).hexdigest()
    return data


def test_oauth_telegram_link_then_login(client, user, monkeypatch):
    monkeypatch.setenv("OAUTH_ENABLED", "1")
    monkeypatch.setenv("TELEGRAM_LOGIN_BOT_TOKEN", "123:fake")
    monkeypatch.setenv("TELEGRAM_LOGIN_BOT_USERNAME", "via_test_bot")
    p = _tg_payload("123:fake")
    # no account yet -> no auto-registration
    assert client.post("/api/auth/oauth/telegram/callback", json=p).status_code == 403
    u = user()
    assert client.post("/api/auth/oauth/telegram/link", json=p, headers=u["h"]).status_code == 200
    me = client.get("/api/auth/me", headers=u["h"]).json()
    assert [x["provider"] for x in me["oauth"]] == ["telegram"]
    r = client.post("/api/auth/oauth/telegram/callback", json=p)
    assert r.status_code == 200 and r.json()["user"]["id"] == u["id"]
    bad = dict(p, hash="0" * 64)
    assert client.post("/api/auth/oauth/telegram/callback", json=bad).status_code == 401
    other = user("o2@test.local")
    assert client.post("/api/auth/oauth/telegram/link", json=p, headers=other["h"]).status_code == 409
    assert client.delete("/api/auth/oauth/telegram", headers=u["h"]).json()["oauth"] == []


def test_oauth_link_all_providers_mocked(client, user, monkeypatch):
    from app.web.routers import oauth
    for p in ("telegram", "google", "vk"):
        monkeypatch.setattr(oauth, {"telegram": "verify_telegram_payload", "google": "verify_google_token",
                                    "vk": "verify_vk_token"}[p],
                            lambda *a, _p=p, **k: {"external_id": f"{_p}-1", "name": "QA"})
    u = user()
    for p in ("telegram", "google", "vk"):
        assert client.post(f"/api/auth/oauth/{p}/link", json={}, headers=u["h"]).status_code == 200
    assert len(client.get("/api/auth/me", headers=u["h"]).json()["oauth"]) == 3
