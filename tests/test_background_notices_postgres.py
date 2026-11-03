"""Фоновое уведомление о проекте клиента на живом Postgres: строка — его, под RLS.

Проход без арендатора видит только строки владельца (0006): инцидент клиента ему не
виден, а уведомление без owner_scope легло бы строкой владельца и клиент его не увидел
бы. Подать Postgres как для tests/test_orch_tenant_postgres.py. Пропускается без
``TEST_DATABASE_URL``.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from tests.orch_pg_support import TEST_DB_URL
from tests.test_orch_tenant_postgres import _scalar_as, pg  # noqa: F401 - фикстура

pytestmark = pytest.mark.skipif(not TEST_DB_URL, reason="TEST_DATABASE_URL не задан — живого Postgres нет")


async def test_incident_notice_is_the_project_clients_row(pg) -> None:  # noqa: F811
    from types import SimpleNamespace

    from app.services import chat_notice
    from app.services.tenant import current_tenant

    a, b = pg["a"].user_id, pg["b"].user_id
    chat_notice.reset()
    mark = uuid.uuid4().hex[:8]
    row = SimpleNamespace(
        details={"title": "Нужно решение"},
        severity="error",
        type="watchdog_stalled",
        project_id=None,
        incident_id=f"inc-pg-{mark}",
        execution_id=f"ex-{mark}",
        tenant_id=a,
    )
    assert current_tenant() is None  # фоновый проход
    status = await chat_notice.incident(row, f"Сторож: зависла работа ({mark})")
    assert status == "written"
    sql = "select count(*) from chat_notices where text like :t"
    assert await _scalar_as(pg["factory"], a, sql, t=f"%{mark}%") == 1
    assert await _scalar_as(pg["factory"], b, sql, t=f"%{mark}%") == 0
    assert await _scalar_as(pg["factory"], None, sql, t=f"%{mark}%") == 0  # не строка владельца
    res = await pg["client"].get("/api/chat-notices", headers=pg["a"].auth)
    assert res.status_code == 200 and mark in res.text
    res_b = await pg["client"].get("/api/chat-notices", headers=pg["b"].auth)
    assert mark not in res_b.text
    _ = text
