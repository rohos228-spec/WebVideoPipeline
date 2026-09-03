"""Тесты Мета-Агента: компилятор промптов и API-эндпоинты."""

from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.services.meta_prompt_compiler import (
    compile_meta_prompt,
    sanitize_compiled_prompt,
)
from app.services.prompt_library import step_dir
from app.web.api import create_app
from app.web.deps import get_session
from tests import accounts_harness as ah


def test_sanitize_compiled_prompt() -> None:
    # 1. Clean markdown fence
    raw = "```markdown\n# Role\nDo task.\n```"
    assert sanitize_compiled_prompt(raw) == "# Role\nDo task."

    # 2. Clean md fence
    raw2 = "```md\n# Prompt\nJSON format\n```"
    assert sanitize_compiled_prompt(raw2) == "# Prompt\nJSON format"

    # 3. Plain text
    raw3 = "System prompt without fences"
    assert sanitize_compiled_prompt(raw3) == "System prompt without fences"


@pytest.mark.asyncio
async def test_compile_meta_prompt_mocked() -> None:
    fake_reply = """# EXCEL GPT MOOD ANALYZER
## Role
Analyze mood of each frame.

## Output Contract (JSON)
```json
[
  { "number": 1, "mood": "tense", "palette": ["#000", "#FFF"] }
]
```
## Constraints
- Strict JSON only. No chat.
"""
    mock_gpt = AsyncMock()
    mock_gpt.ask_fresh.return_value = fake_reply

    with patch("app.services.meta_prompt_compiler.get_gpt_client", return_value=mock_gpt):
        result = await compile_meta_prompt(
            step_code="excel_gpt",
            user_intent="Анализируй настроение каждого кадра и цветовую палитру",
            project_topic="Киберпанк 2050",
            target_name="mood_analyzer",
        )

        assert result["step_code"] == "excel_gpt"
        assert result["name"] == "mood_analyzer"
        assert "EXCEL GPT MOOD ANALYZER" in result["compiled_prompt"]
        assert result["stats"]["has_schema"] is True
        assert result["stats"]["has_guards"] is True


@pytest_asyncio.fixture
async def env(tmp_path, monkeypatch):
    """Студия с identity и промтами во временной папке.

    У заказчика тест ходил в реальный prompts/ и писал туда файл; у нас
    API за IdentityMiddleware, а prompts/ — read-only бинд на проде.
    """
    from app.services import prompt_library as pl

    monkeypatch.setattr(pl, "PROMPTS_ROOT", tmp_path / "prompts")
    ah.configure(monkeypatch)
    engine, factory = await ah.make_engine(tmp_path / "meta.db")
    ah.bind_identity_session(monkeypatch, factory)

    async def _gen():
        async with factory() as s:
            yield s

    app = create_app()
    app.dependency_overrides[get_session] = _gen
    admin = await ah.make_admin(factory)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield {"client": c, "admin": admin}
    await engine.dispose()


async def test_meta_agent_endpoints(env, tmp_path) -> None:
    client = env["client"]
    auth = env["admin"].auth
    fake_reply = "# TEST HERO STYLE\n[character_description]\nStrict 8k render."
    mock_gpt = AsyncMock()
    mock_gpt.ask_fresh.return_value = fake_reply

    with patch("app.services.meta_prompt_compiler.get_gpt_client", return_value=mock_gpt):
        resp = await client.post(
            "/api/meta-agent/compile",
            json={
                "step_code": "hero_style",
                "user_intent": "Аниме стиль в духе Макото Синкая",
                "target_name": "anime_shinkai",
            },
            headers=auth,
        )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["ok"] is True
    assert "TEST HERO STYLE" in data["compiled_prompt"]

    save_resp = await client.post(
        "/api/meta-agent/save-and-activate",
        json={
            "step_code": "hero_style",
            "name": "test_meta_preset",
            "content": "# Test content\n8k anime style",
            "activate": False,
        },
        headers=auth,
    )
    assert save_resp.status_code == 200, save_resp.text
    save_data = save_resp.json()
    assert save_data["ok"] is True
    assert save_data["name"] == "test_meta_preset"
    assert save_data["file_name"] == "test_meta_preset.md"
    # Файл лёг во временный prompts/, а не в репозиторий.
    assert (step_dir("hero_style") / "test_meta_preset.md").exists()
    assert str(step_dir("hero_style")).startswith(str(tmp_path))
