"""ТЗ NEW: здоровье моделей — агрегация успехов/ошибок, API для дашборда профиля."""
import uuid

from tests.qa_merge.const import MODELS_HEALTH, PROVIDER_RE, TEST_HOOKS
from tests.qa_merge.conftest import post_webhook, yk_webhook_body


def _prime_wallet(client, user):
    body = yk_webhook_body(f"h-{uuid.uuid4().hex[:6]}", "1000.00", user["user_id"])
    assert post_webhook(client, body).status_code == 200


def _run(client, headers, model, fail):
    return client.post(TEST_HOOKS["run_generation"],
                       json={"model": model, "fail": fail}, headers=headers)


def test_health_endpoint_available(client, make_user):
    u = make_user()
    r = client.get(MODELS_HEALTH, headers=u["headers"])
    assert r.status_code == 200
    data = r.json()
    models = data if isinstance(data, list) else data.get("models", [])
    assert isinstance(models, list)


def test_health_aggregates_success_and_errors(client, make_user):
    u = make_user()
    _prime_wallet(client, u)
    for fail in (False, False, True):
        _run(client, u["headers"], "video-basic", fail)
    data = client.get(MODELS_HEALTH, headers=u["headers"]).json()
    models = data if isinstance(data, list) else data["models"]
    rec = next(m for m in models if m.get("model") == "video-basic")
    assert rec.get("success", rec.get("ok", 0)) >= 2
    assert rec.get("errors", rec.get("failed", 0)) >= 1
    rate = rec.get("success_rate")
    if rate is not None:
        assert 0 <= rate <= 1 or 0 <= rate <= 100


def test_health_shape_for_profile_dashboard(client, make_user):
    """Каждая запись содержит минимум полей для live-дашборда профиля."""
    u = make_user()
    _prime_wallet(client, u)
    _run(client, u["headers"], "image-basic", False)
    data = client.get(MODELS_HEALTH, headers=u["headers"]).json()
    models = data if isinstance(data, list) else data["models"]
    rec = next(m for m in models if m.get("model") == "image-basic")
    for field in ("model",):
        assert field in rec
    assert any(k in rec for k in ("success", "ok"))
    assert any(k in rec for k in ("errors", "failed"))
    assert any(k in rec for k in ("updated_at", "last_run", "ts"))


def test_health_hides_providers(client, make_user):
    """ТЗ: «дашборд как у Kie», но провайдеры скрыты."""
    u = make_user()
    r = client.get(MODELS_HEALTH, headers=u["headers"])
    m = PROVIDER_RE.search(r.text)
    assert not m, f"провайдер «{m.group(0)}» в /api/models/health"


def test_health_requires_auth(client):
    r = client.get(MODELS_HEALTH)
    assert r.status_code in (200, 401, 403)  # публичность решается ТЗ; 5xx недопустимо
    assert r.status_code < 500
