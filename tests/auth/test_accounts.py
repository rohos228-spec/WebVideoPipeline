import re
import sqlite3


def test_accounts_schema_tables(client, user, tmp_path):
    user()
    names = {r[0] for r in sqlite3.connect(tmp_path / "accounts.db").execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"users", "sessions", "tokens", "oauth_links", "schema_migrations"} <= names


def test_accounts_full_flow(client, outbox):
    r = client.post("/api/auth/register", json={"email": "Flow@Test.local", "password": "Passw0rd!"})
    assert r.status_code == 200 and r.json()["user"]["email"] == "flow@test.local"
    assert r.json()["user"]["email_verified"] is False
    mail = outbox[-1]
    assert mail["to"] == "flow@test.local" and "token=" in mail["body"] and "<a href" in mail["html"]
    code = re.search(r"код: (\d{6})", mail["body"]).group(1)
    h = {"Authorization": f"Bearer {r.json()['token']}"}
    assert client.post("/api/auth/verify", json={"code": "000000" if code != "000000" else "111111"}, headers=h).status_code == 400
    assert client.post("/api/auth/verify", json={"code": code}, headers=h).status_code == 200
    me = client.get("/api/auth/me", headers=h).json()
    assert me["email_verified"] is True and me["role"] == "user"
    assert client.post("/api/auth/logout", headers=h).status_code == 200
    assert client.get("/api/auth/me", headers=h).status_code == 401


def test_accounts_weak_password_and_bad_email(client):
    assert client.post("/api/auth/register", json={"email": "x@test.local", "password": "short"}).status_code == 422
    assert client.post("/api/auth/register", json={"email": "nope", "password": "Passw0rd!"}).status_code == 422


def test_accounts_login_by_username_legacy_field(client, user):
    user("bob@test.local")
    r = client.post("/api/auth/login", json={"username": "bob", "password": "Passw0rd!"})
    assert r.status_code == 200


def test_accounts_change_password_drops_other_sessions(client, user):
    u = user()
    other = client.post("/api/auth/login", json={"email": u["email"], "password": "Passw0rd!"}).json()["token"]
    r = client.post("/api/auth/change-password", json={"old_password": "Passw0rd!", "new_password": "N3wPassword"},
                    headers=u["h"])
    assert r.status_code == 200
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {other}"}).status_code == 401


def test_accounts_legacy_env_login(app_factory, monkeypatch):
    from app.settings import settings
    monkeypatch.setattr(settings, "web_auth_user", "admin", raising=False)
    monkeypatch.setattr(settings, "web_auth_password", "secret", raising=False)
    with app_factory(False) as c:
        r = c.post("/api/auth/login", json={"username": "admin", "password": "secret"})
        assert r.status_code == 200
        me = c.get("/api/auth/me", headers={"Authorization": f"Bearer {r.json()['token']}"}).json()
        assert me["legacy"] is True


def test_accounts_mail_has_no_provider_names(client, outbox):
    client.post("/api/auth/register", json={"email": "p@test.local", "password": "Passw0rd!"})
    client.post("/api/auth/forgot-password", json={"email": "p@test.local"})
    txt = " ".join(m["body"] + (m["html"] or "") for m in outbox).lower()
    for w in ("52pay", "kie", "outsee", "vibecode", "openai", "anthropic"):
        assert w not in txt
