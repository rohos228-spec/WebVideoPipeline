"""Тесты корректной загрузки и разрешения арендаторских промтов (RLS bypass + worker scope)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services import prompt_store
from app.services.prompt_library import read_resolved_project_prompt
from app.services.prompt_store import PromptScope
from app.services.studio_auth import StudioIdentity, set_identity
from app.services.tenant import current_tenant, tenant_scope
from app.settings import settings


@pytest.fixture(autouse=True)
def clean_state():
    prompt_store.reset_cache()
    set_identity(None)
    with tenant_scope(None):
        yield
    prompt_store.reset_cache()
    set_identity(None)


def test_admin_sees_tenant_prompts_in_list_names():
    tenant_a = "019298b4-e110-4819-b116-e1b212dfb0df"
    prompt_store._CACHE[(tenant_a, "", None, "img_pr", "06_plastilin_prompt_volumetric_db")] = "clay prompt"
    prompt_store._CACHE[(None, "", None, "img_pr", "default")] = "default prompt"

    # Обычный участник без арендатора видит только дефолт
    assert prompt_store.list_names("img_pr", PromptScope()) == ["default"]

    # Участник A видит свой промт и дефолт
    assert "06_plastilin_prompt_volumetric_db" in prompt_store.list_names("img_pr", PromptScope(tenant_id=tenant_a))

    # Админ видит промты всех арендаторов
    admin_id = StudioIdentity(
        user_id="admin-1",
        email="admin@studio.local",
        role="admin",
        epoch=1,
    )
    set_identity(admin_id)
    assert "06_plastilin_prompt_volumetric_db" in prompt_store.list_names("img_pr", PromptScope())


def test_worker_fallback_resolves_tenant_prompt():
    tenant_a = "019298b4-e110-4819-b116-e1b212dfb0df"
    prompt_store._CACHE[(tenant_a, "", None, "img_pr", "06_plastilin_prompt_volumetric_db")] = "clay prompt"

    # Воркер без контекста арендатора (sc.tenant_id is None)
    text, source = prompt_store.resolve_with_source("img_pr", "06_plastilin_prompt_volumetric_db", PromptScope())
    assert text == "clay prompt"
    assert source == "tenant"


def test_read_resolved_project_prompt_inherits_project_tenant():
    tenant_a = "019298b4-e110-4819-b116-e1b212dfb0df"
    prompt_store._CACHE[(tenant_a, "", None, "img_pr", "06_plastilin_prompt_volumetric_db")] = "clay prompt"
    prompt_store._CACHE[(None, "", None, "img_pr", "default")] = "default prompt"

    class DummyProject:
        id = 46
        tenant_id = tenant_a
        prompt_overrides = {"img_pr": "06_plastilin_prompt_volumetric_db"}
        meta = {}
        topic = "Тестовый топик"

    # Контекст воркера: current_tenant() равен None
    assert current_tenant() is None

    # read_resolved_project_prompt должен вернуть кастомный промт проекта, а не упасть на default!
    name, path, text, source = read_resolved_project_prompt(DummyProject(), "img_pr")
    assert name == "06_plastilin_prompt_volumetric_db"
    assert source == "override"
    assert "clay prompt" in text


@pytest.mark.asyncio
async def test_refresh_sets_is_admin_on_postgres(monkeypatch):
    monkeypatch.setattr(type(settings), "is_postgres", property(lambda self: True))
    mock_session = AsyncMock()
    mock_execute_result = MagicMock()
    mock_execute_result.scalars.return_value.all.return_value = []
    mock_session.execute.return_value = mock_execute_result

    await prompt_store.refresh(mock_session)

    # Проверяем, что была вызвана команда установки app.is_admin = 'true'
    calls = [str(call.args[0]) for call in mock_session.execute.call_args_list if call.args]
    assert any("set_config('app.is_admin', 'true', true)" in c for c in calls)
