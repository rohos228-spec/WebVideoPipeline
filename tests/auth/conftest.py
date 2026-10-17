import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def outbox(monkeypatch):
    box: list[dict] = []
    from app.services.user_accounts import mailer
    monkeypatch.setattr(mailer, "send_mail", lambda to, subject, body, **kw: box.append(
        {"to": to, "subject": subject, "body": body, "html": kw.get("html")}))
    return box


@pytest.fixture()
def app_factory(tmp_path, monkeypatch, outbox):
    monkeypatch.setenv("VIA_ACCOUNTS_DB", str(tmp_path / "accounts.db"))
    monkeypatch.setenv("VIA_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("OAUTH_ENABLED", raising=False)

    def make(auth: bool = True):
        monkeypatch.setenv("VIA_AUTH_ENABLED", "1" if auth else "0")
        from app.web.api import create_app
        return TestClient(create_app(), raise_server_exceptions=False)
    return make


@pytest.fixture()
def client(app_factory):
    with app_factory(True) as c:
        yield c


@pytest.fixture()
def user(client, outbox):
    import re

    def make(email="a@test.local", pw="Passw0rd!", verify=True):
        r = client.post("/api/auth/register", json={"email": email, "password": pw})
        assert r.status_code == 200, r.text
        if verify:
            tok = re.search(r"token=([\w\-]+)", outbox[-1]["body"]).group(1)
            assert client.post("/api/auth/verify-email", json={"token": tok}).status_code == 200
        t = client.post("/api/auth/login", json={"email": email, "password": pw}).json()["token"]
        h = {"Authorization": f"Bearer {t}"}
        return {"email": email, "h": h, "id": client.get("/api/auth/me", headers=h).json()["id"]}
    return make
