"""ТЗ п.35: роли owner/admin/user/member."""
import pytest

from tests.qa_merge.const import ADMIN_USERS, PROFILE


def _set_role_direct(client, actor_headers, user_id, role):
    return client.patch(PROFILE["set_role"].format(user_id=user_id),
                        json={"role": role}, headers=actor_headers)


@pytest.fixture()
def owner(client, make_user, tmp_path, monkeypatch):
    """Первый зарегистрированный пользователь — owner (или назначается через env VIA_OWNER_EMAIL)."""
    monkeypatch.setenv("VIA_OWNER_EMAIL", "owner@test.local")
    return make_user(email="owner@test.local")


def test_default_role_is_user(client, make_user):
    u = make_user()
    me = client.get(PROFILE["me"], headers=u["headers"]).json()
    assert me.get("role") in ("user", "member") and me.get("role") != "admin"


def test_admin_endpoint_forbidden_for_user(client, make_user):
    u = make_user()
    assert client.get(ADMIN_USERS, headers=u["headers"]).status_code in (401, 403)


def test_owner_can_promote_to_admin(client, owner, make_user):
    u = make_user()
    r = _set_role_direct(client, owner["headers"], u["user_id"], "admin")
    assert r.status_code == 200, r.text
    me = client.get(PROFILE["me"], headers=u["headers"]).json()
    assert me["role"] == "admin"
    assert client.get(ADMIN_USERS, headers=u["headers"]).status_code == 200


def test_admin_cannot_change_owner(client, owner, make_user):
    u = make_user()
    _set_role_direct(client, owner["headers"], u["user_id"], "admin")
    r = _set_role_direct(client, u["headers"], owner["user_id"], "user")
    assert r.status_code in (400, 403)


def test_user_cannot_escalate_own_role(client, make_user):
    u = make_user()
    r = _set_role_direct(client, u["headers"], u["user_id"], "owner")
    assert r.status_code in (400, 401, 403)
    me = client.get(PROFILE["me"], headers=u["headers"]).json()
    assert me["role"] != "owner"


def test_member_has_no_billing_admin_rights(client, owner, make_user):
    """member (команда «Завода») не управляет ролями и не видит админку."""
    u = make_user()
    _set_role_direct(client, owner["headers"], u["user_id"], "member")
    assert client.get(ADMIN_USERS, headers=u["headers"]).status_code in (401, 403)
    other = make_user()
    assert _set_role_direct(client, u["headers"], other["user_id"], "admin").status_code in (401, 403)
