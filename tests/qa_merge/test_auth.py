"""ТЗ п.33–34 (почта, подтверждение), п.36 (OAuth-привязка), сессии."""
import importlib
import uuid

import pytest

from tests.qa_merge.const import AUTH, OAUTH_VERIFY
from tests.qa_merge.conftest import _extract_token, _patch_dotted


def test_register_sends_verification_email(client, outbox):
    email = f"reg{uuid.uuid4().hex[:6]}@test.local"
    r = client.post(AUTH["register"], json={"email": email, "password": "Str0ng!pass"})
    assert r.status_code in (200, 201)
    assert outbox and outbox[-1]["to"] == email
    assert "token=" in outbox[-1]["body"]


def test_register_duplicate_email_rejected(client, make_user):
    u = make_user()
    r = client.post(AUTH["register"], json={"email": u["email"], "password": "Other1!pass"})
    assert r.status_code in (400, 409)


def test_verify_email_ok_and_bad_token(client, outbox):
    email = f"v{uuid.uuid4().hex[:6]}@test.local"
    client.post(AUTH["register"], json={"email": email, "password": "Str0ng!pass"})
    token = _extract_token(outbox[-1])
    assert client.post(AUTH["verify"], json={"token": token}).status_code == 200
    # мусорный токен
    assert client.post(AUTH["verify"], json={"token": "garbage"}).status_code in (400, 401, 404)
    # повторное использование токена не должно падать 500
    assert client.post(AUTH["verify"], json={"token": token}).status_code < 500


def test_login_success_and_me(client, make_user):
    u = make_user()
    me = client.get(AUTH["me"], headers=u["headers"])
    assert me.status_code == 200
    assert me.json().get("email") == u["email"]


def test_login_wrong_password(client, make_user):
    u = make_user()
    r = client.post(AUTH["login"], json={"email": u["email"], "password": "WRONG!pass1"})
    assert r.status_code in (400, 401, 403)


def test_password_reset_flow(client, make_user, outbox):
    u = make_user()
    r = client.post(AUTH["forgot"], json={"email": u["email"]})
    assert r.status_code == 200
    token = _extract_token(outbox[-1])
    r = client.post(AUTH["reset"], json={"token": token, "password": "NewStr0ng!pass"})
    assert r.status_code == 200
    # старый пароль больше не работает, новый — работает
    assert client.post(AUTH["login"], json={"email": u["email"], "password": u["password"]}).status_code in (400, 401, 403)
    assert client.post(AUTH["login"], json={"email": u["email"], "password": "NewStr0ng!pass"}).status_code == 200


def test_password_reset_token_single_use(client, make_user, outbox):
    u = make_user()
    client.post(AUTH["forgot"], json={"email": u["email"]})
    token = _extract_token(outbox[-1])
    assert client.post(AUTH["reset"], json={"token": token, "password": "NewStr0ng!pass"}).status_code == 200
    r = client.post(AUTH["reset"], json={"token": token, "password": "Another1!pass"})
    assert r.status_code in (400, 401, 404)


def test_forgot_password_unknown_email_no_leak(client):
    """Ответ не должен раскрывать существование почты (200 без деталей)."""
    r = client.post(AUTH["forgot"], json={"email": "nobody@test.local"})
    assert r.status_code in (200, 202)


def test_logout_invalidates_session(client, make_user):
    u = make_user()
    assert client.post(AUTH["logout"], headers=u["headers"]).status_code in (200, 204)
    assert client.get(AUTH["me"], headers=u["headers"]).status_code in (401, 403)


def test_sessions_list_and_revoke(client, make_user):
    u = make_user()
    # вторая сессия
    client.post(AUTH["login"], json={"email": u["email"], "password": u["password"]})
    r = client.get(AUTH["sessions"], headers=u["headers"])
    assert r.status_code == 200
    sessions = r.json() if isinstance(r.json(), list) else r.json().get("sessions", [])
    assert len(sessions) >= 2
    other = next(s for s in sessions if not s.get("current"))
    r = client.delete(f'{AUTH["sessions"]}/{other["id"]}', headers=u["headers"])
    assert r.status_code in (200, 204)
    r = client.get(AUTH["sessions"], headers=u["headers"])
    ids = [s["id"] for s in (r.json() if isinstance(r.json(), list) else r.json()["sessions"])]
    assert other["id"] not in ids


@pytest.mark.parametrize("provider,payload", [
    ("telegram", {"id": 777, "username": "qa_tg", "hash": "mock"}),
    ("google",   {"id_token": "mock-google-token"}),
    ("vk",       {"access_token": "mock-vk-token", "user_id": 555}),
])
def test_oauth_link_in_profile(client, make_user, monkeypatch, provider, payload):
    """Привязка соцсети в профиле с замоканной проверкой провайдера (без сети)."""
    _patch_dotted(monkeypatch, OAUTH_VERIFY[provider],
                  lambda *a, **kw: {"external_id": f"{provider}-ext-1", "name": "QA"})
    u = make_user()
    r = client.post(AUTH["oauth_link"].format(provider=provider),
                    json=payload, headers=u["headers"])
    assert r.status_code == 200, r.text
    me = client.get(AUTH["me"], headers=u["headers"]).json()
    linked = me.get("oauth") or me.get("linked_accounts") or []
    assert any(provider in str(x).lower() for x in linked)


def test_oauth_cannot_create_account_without_email(client, monkeypatch):
    """ТЗ п.36: новый аккаунт только через email — OAuth без сессии не регистрирует."""
    _patch_dotted(monkeypatch, OAUTH_VERIFY["telegram"],
                  lambda *a, **kw: {"external_id": "tg-anon", "name": "Anon"})
    r = client.post(AUTH["oauth_link"].format(provider="telegram"),
                    json={"id": 1, "hash": "mock"})  # без авторизации
    assert r.status_code in (401, 403)


def test_protected_api_requires_auth(client):
    assert client.get(AUTH["me"]).status_code in (401, 403)
