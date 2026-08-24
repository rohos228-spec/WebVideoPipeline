"""Перепривязка арендатора внутри открытой транзакции.

`after_begin` ставит `SET LOCAL app.tenant_id` один раз — в начале транзакции.
Этого хватает всем, кто получает сессию уже с назначенным арендатором, и не
хватает одному случаю: когда арендатор появляется ПОСЛЕ первого запроса. Ровно
так устроено заведение учётки — сперва «адрес не занят», потом генерация UUID,
и только потом вставка счёта, которая уже под политикой RLS.

Настоящую проверку делает `tests/test_rls_postgres.py` на живом Postgres:
только сервер может подтвердить, что политика приняла строку. Здесь —
механика, которая должна быть покрыта и на машине без Postgres: какой SQL
уходит, с каким параметром и когда не уходит вовсе.
"""

from __future__ import annotations

import pytest

from app.db import rebind_tenant
from app.services.tenant import TenantIsolationError, tenant_scope
from app.settings import settings

TENANT = "7c9e6679-7425-40de-944b-e07fc1f90ae7"


class _FakeSession:
    """Считает, что через неё выполнили. Ничего не исполняет."""

    def __init__(self) -> None:
        self.executed: list[tuple[str, dict]] = []

    async def execute(self, statement, params=None):
        self.executed.append((str(statement), params or {}))
        return None


async def test_sqlite_issues_no_sql(monkeypatch) -> None:
    """На SQLite привязывать нечего: `SET LOCAL` там не существует."""
    monkeypatch.setattr(settings, "database_url", "")
    monkeypatch.setattr(settings, "allow_unisolated_tenants", True)
    session = _FakeSession()
    with tenant_scope(TENANT):
        assert await rebind_tenant(session) == TENANT
    assert session.executed == []


async def test_postgres_sets_the_tenant(monkeypatch) -> None:
    """На Postgres уходит ровно один `set_config` с арендатором параметром.

    Параметром, а не подстановкой в текст: `SET LOCAL` плейсхолдера не
    принимает, поэтому используется `set_config(..., is_local => true)`.
    Подставлять значение в SQL не хочется даже проверенное.
    """
    monkeypatch.setattr(settings, "database_url", "postgresql+asyncpg://a:b@127.0.0.1/x")
    session = _FakeSession()
    with tenant_scope(TENANT):
        assert await rebind_tenant(session) == TENANT

    assert len(session.executed) == 1
    sql, params = session.executed[0]
    assert "set_config" in sql
    assert "app.tenant_id" in sql
    assert params == {"tid": TENANT}


async def test_without_a_tenant_nothing_happens(monkeypatch) -> None:
    """Режим владельца: арендатора нет, привязывать некого."""
    monkeypatch.setattr(settings, "database_url", "postgresql+asyncpg://a:b@127.0.0.1/x")
    session = _FakeSession()
    with tenant_scope(None):
        assert await rebind_tenant(session) is None
    assert session.executed == []


async def test_multi_tenant_on_sqlite_is_refused(monkeypatch) -> None:
    """`require_isolation` работает и здесь: арендатор без RLS — это дыра.

    Ветка важна: `rebind_tenant` зовут из заведения учётки, то есть по пути,
    который в SaaS обязан быть на Postgres. Молча привязать арендатора на
    SQLite значило бы завести пользователя в системе без изоляции.
    """
    monkeypatch.setattr(settings, "database_url", "")
    monkeypatch.setattr(settings, "allow_unisolated_tenants", False)
    session = _FakeSession()
    with tenant_scope(TENANT), pytest.raises(TenantIsolationError):
        await rebind_tenant(session)
    assert session.executed == []
