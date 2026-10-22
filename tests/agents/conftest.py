"""Фикстуры ядра оркестратора. БД — временный sqlite из общего conftest.

``ORCH_TEST_ON_POSTGRES=1`` вместе с ``TEST_DATABASE_URL`` гонит эти же тесты
на живом Postgres: таблицы движка (задачи, события, сообщения) уходят туда,
схема — миграциями alembic, перед каждым тестом таблицы пустые. Остальная
база приложения в тестах остаётся временным SQLite.
"""

from __future__ import annotations

import pytest

from app.orchestrator.agents.runtime import build_runtime, set_runtime
from tests.agents.mocks import MockActions, MockValidators, ScriptedRouter
from tests.orch_pg_support import ENGINE_TESTS_ON_POSTGRES, TEST_DB_URL, prepare_postgres, reset_engine_state

TEST_TOKEN = "orch-test-token"


@pytest.fixture(autouse=True)
def _engine_tables_on_postgres(monkeypatch: pytest.MonkeyPatch):
    if not ENGINE_TESTS_ON_POSTGRES:
        yield
        return
    from app.settings import settings

    monkeypatch.setattr(settings, "database_url", TEST_DB_URL)
    reset_engine_state()
    prepare_postgres()
    yield
    reset_engine_state()


@pytest.fixture
def token(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv("ORCH_API_TOKEN", TEST_TOKEN)
    return TEST_TOKEN


@pytest.fixture
def actions() -> MockActions:
    return MockActions()


@pytest.fixture
def scripted() -> ScriptedRouter:
    return ScriptedRouter()


@pytest.fixture
def runtime(token: str, actions: MockActions, scripted: ScriptedRouter):
    del token
    rt = build_runtime(actions=actions, validators=MockValidators(), router=scripted)
    set_runtime(rt)
    yield rt
    set_runtime(None)
