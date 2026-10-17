"""ТЗ п.22: имён провайдеров нет ни в одном ответе API, ошибке, уведомлении, отчёте."""
import uuid

import pytest

from tests.qa_merge.const import (
    AUTH, BILLING, MODELS_HEALTH, PROFILE, PROVIDER_RE, TEST_HOOKS,
)
from tests.qa_merge.conftest import post_webhook, yk_webhook_body


def assert_clean(text: str, where: str):
    m = PROVIDER_RE.search(text or "")
    assert not m, f"провайдер «{m.group(0)}» утёк в {where}"


PUBLIC_GETS = [
    BILLING["plans"],
    MODELS_HEALTH,
]
AUTHED_GETS = [
    AUTH["me"],
    PROFILE["me"],
    BILLING["balance"],
    BILLING["ledger"],
    BILLING["subscription"],
    MODELS_HEALTH,
]


@pytest.mark.parametrize("path", PUBLIC_GETS)
def test_no_providers_in_public_responses(client, path):
    r = client.get(path)
    assert r.status_code < 500
    assert_clean(r.text, f"GET {path}")


def test_no_providers_in_authed_responses(client, make_user):
    u = make_user()
    for path in AUTHED_GETS:
        r = client.get(path, headers=u["headers"])
        assert r.status_code < 500
        assert_clean(r.text, f"GET {path}")


def test_no_providers_in_error_bodies(client, make_user):
    """Ошибки валидации, 404, 402 и ошибка генерации — без имён провайдеров."""
    u = make_user()
    checks = [
        client.post(AUTH["login"], json={"email": "x"}),                 # 422
        client.get("/api/definitely/not/here"),                          # 404
        client.post(BILLING["checkout"], json={"plan": "nope"},
                    headers=u["headers"]),                               # 400/404
        client.post(TEST_HOOKS["run_generation"],
                    json={"model": "video-basic", "fail": True},
                    headers=u["headers"]),                               # ошибка генерации
    ]
    for r in checks:
        assert_clean(r.text, f"{r.request.method} {r.url} -> {r.status_code}")


def test_no_providers_in_notifications(client, make_user):
    u = make_user()
    # провоцируем событие: неуспешная генерация -> уведомление об ошибке и возврате
    body = yk_webhook_body(f"n-{uuid.uuid4().hex[:6]}", "500.00", u["user_id"])
    post_webhook(client, body)
    client.post(TEST_HOOKS["run_generation"],
                json={"model": "video-basic", "fail": True}, headers=u["headers"])
    r = client.get(TEST_HOOKS["notify"], headers=u["headers"])
    assert r.status_code == 200
    assert_clean(r.text, "последнее уведомление пользователю")


def test_no_providers_in_reports(client, make_user):
    u = make_user()
    r = client.get(TEST_HOOKS["report"], headers=u["headers"])
    assert r.status_code == 200
    assert_clean(r.text, "последний отчёт пользователю")


def test_no_providers_in_openapi_schema(client):
    """Схема OpenAPI публична — имена провайдеров недопустимы и в ней."""
    r = client.get("/openapi.json")
    if r.status_code == 404:
        pytest.skip("OpenAPI отключён — ок")
    assert_clean(r.text, "/openapi.json")


def test_no_providers_in_email_bodies(client, outbox):
    email = f"p{uuid.uuid4().hex[:6]}@test.local"
    client.post(AUTH["register"], json={"email": email, "password": "Str0ng!pass"})
    for mail in outbox:
        assert_clean(mail["subject"] + "\n" + mail["body"], f"письмо к {mail['to']}")
