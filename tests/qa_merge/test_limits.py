"""ТЗ п.35, п.44: жёсткие лимиты потоков по тарифу — лишнее ждёт в очереди."""
import uuid

from tests.qa_merge.const import BILLING, TEST_HOOKS
from tests.qa_merge.conftest import post_webhook, yk_webhook_body


def _activate_plan(client, user, plan):
    body = yk_webhook_body(f"lim-{uuid.uuid4().hex[:6]}", "1990.00",
                           user["user_id"], purpose="subscription",
                           plan=plan, period="month")
    assert post_webhook(client, body).status_code == 200


def _enqueue(client, headers, count):
    r = client.post(TEST_HOOKS["enqueue"], json={"count": count}, headers=headers)
    assert r.status_code == 200, r.text


def _queue(client, headers):
    r = client.get(TEST_HOOKS["queue_state"], headers=headers)
    assert r.status_code == 200
    return r.json()  # {"running": N, "waiting": M, "limit": L}


def test_plan_exposes_thread_limit(client, make_user):
    u = make_user()
    _activate_plan(client, u, "start")
    sub = client.get(BILLING["subscription"], headers=u["headers"]).json()
    assert isinstance(sub.get("thread_limit"), int) and sub["thread_limit"] >= 1


def test_hard_limit_excess_waits_in_queue(client, make_user):
    """Лимит жёсткий: задач сверх лимита нет в running, они в waiting, не отклонены."""
    u = make_user()
    _activate_plan(client, u, "start")
    limit = client.get(BILLING["subscription"], headers=u["headers"]).json()["thread_limit"]
    _enqueue(client, u["headers"], limit + 3)
    q = _queue(client, u["headers"])
    assert q["running"] <= limit
    assert q["waiting"] >= 3
    assert q["running"] + q["waiting"] == limit + 3, "задачи не должны отбрасываться"


def test_limit_grows_with_plan_upgrade(client, make_user):
    u = make_user()
    _activate_plan(client, u, "start")
    low = client.get(BILLING["subscription"], headers=u["headers"]).json()["thread_limit"]
    _activate_plan(client, u, "factory")
    high = client.get(BILLING["subscription"], headers=u["headers"]).json()["thread_limit"]
    assert high > low


def test_no_plan_minimal_limit(client, make_user):
    """Без подписки — минимальный лимит (>=1), но не безлимит."""
    u = make_user()
    sub = client.get(BILLING["subscription"], headers=u["headers"]).json()
    limit = sub.get("thread_limit", 1)
    assert 1 <= limit <= 2
