def test_roles_owner_from_env(client, user, monkeypatch):
    monkeypatch.setenv("VIA_OWNER_EMAIL", "boss@test.local")
    o = user("boss@test.local")
    assert client.get("/api/profile/me", headers=o["h"]).json()["role"] == "owner"
    assert client.get("/api/admin/users", headers=o["h"]).status_code == 200


def test_roles_user_forbidden_admin(client, user):
    u = user()
    assert client.get("/api/admin/users", headers=u["h"]).status_code == 403
    assert client.get("/api/profile/roles", headers=u["h"]).status_code == 403


def test_roles_matrix():
    from app.services import roles
    o, a, u, m = ({"id": i, "role": r} for i, r in enumerate(("owner", "admin", "user", "member")))
    assert roles.can_change_role(o, u, "admin")
    assert not roles.can_change_role(o, u, "owner")
    assert not roles.can_change_role(a, o, "user")
    assert not roles.can_change_role(a, {"id": 9, "role": "admin"}, "user")
    assert roles.can_change_role(a, u, "member")
    assert not roles.can_change_role(m, u, "admin")
    assert not roles.can_change_role(u, u, "owner")


def test_roles_team_requires_zavod_plan(client, user):
    from app.services import user_accounts as ua
    boss, mate = user("boss2@test.local"), user("mate@test.local")
    r = client.post("/api/profile/team", json={"email": "mate@test.local"}, headers=boss["h"])
    assert r.status_code == 403
    ua.set_plan(boss["id"], "zavod")
    r = client.post("/api/profile/team", json={"email": "mate@test.local"}, headers=boss["h"])
    assert r.status_code == 200, r.text
    me = client.get("/api/profile/me", headers=mate["h"]).json()
    assert me["role"] == "member" and me["effective_plan"] == "zavod"
    assert client.post("/api/profile/team", json={"email": "boss2@test.local"}, headers=mate["h"]).status_code == 403


def test_roles_user_context_for_limits(client, user):
    from starlette.requests import Request

    from app.services import roles
    u = user()
    req = Request({"type": "http", "headers": [(b"authorization", u["h"]["Authorization"].encode())]})
    ctx = roles.user_context(req)
    assert ctx["user_id"] == u["id"] and ctx["role"] == "user" and ctx["email_verified"] is True


def test_roles_require_verified(client, user):
    import pytest
    from fastapi import HTTPException
    from starlette.requests import Request

    from app.services import roles
    u = user("nv@test.local", verify=False)
    req = Request({"type": "http", "headers": [(b"authorization", u["h"]["Authorization"].encode())]})
    with pytest.raises(HTTPException) as e:
        roles.require_verified_user(req)
    assert e.value.status_code == 403
