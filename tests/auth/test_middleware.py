import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def gated(app_factory):
    """create_app() + AuthGateMiddleware (until A registers it in api.py the test adds it itself)."""
    def make(auth):
        c = app_factory(auth)
        from app.web.middleware_auth import AuthGateMiddleware
        if not any(m.cls is AuthGateMiddleware for m in c.app.user_middleware):
            c.app.add_middleware(AuthGateMiddleware)
        return TestClient(c.app, raise_server_exceptions=False)
    return make


def test_middleware_blocks_without_session(gated):
    with gated(True) as c:
        assert c.get("/api/projects").status_code == 401
        assert c.get("/api/auth/status").status_code == 200


def test_middleware_off_by_default(gated):
    with gated(False) as c:
        assert c.get("/api/projects").status_code != 401


def test_middleware_allows_session(gated, outbox):
    with gated(True) as c:
        t = c.post("/api/auth/register", json={"email": "m@test.local", "password": "Passw0rd!"}).json()["token"]
        assert c.get("/api/projects", headers={"Authorization": f"Bearer {t}"}).status_code != 401
