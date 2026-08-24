"""Редактор промтов клиента: то место, куда он идёт «менять промпт».

Ручки `/api/prompts` и `/api/prompt-files` для этого не годятся: они правят
промты ПЛАТФОРМЫ, одни на всех, и потому закрыты от роли `member`. Здесь у
участника свой вход, пишущий в его область.
"""

from __future__ import annotations

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.services import prompt_store
from app.settings import settings
from app.web.api import create_app
from app.web.deps import get_session
from tests import accounts_harness as ah


@pytest_asyncio.fixture
async def env(tmp_path, monkeypatch):
    ah.configure(monkeypatch)
    monkeypatch.setattr(settings, "studio_brand", "multik")
    prompt_store.reset_cache()

    engine, factory = await ah.make_engine(tmp_path / "mp.db")
    ah.bind_identity_session(monkeypatch, factory)

    async def _gen():
        async with factory() as s:
            yield s

    app = create_app()
    app.dependency_overrides[get_session] = _gen

    mine = await ah.make_account(factory, email="mine@studio.local")
    neighbour = await ah.make_account(factory, email="neighbour@studio.local")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        c.headers["Authorization"] = f"Bearer {mine.token}"
        yield {"client": c, "mine": mine, "neighbour": neighbour}
    prompt_store.reset_cache()
    await engine.dispose()


@pytest_asyncio.fixture
async def client(env):
    """Совместимость с прежним именем фикстуры — тела тестов не переписываем."""
    return env["client"]


async def test_editing_my_prompt_does_not_touch_the_platform(client) -> None:
    """Правка уходит в область клиента, а не в системную.

    Иначе один клиент поменял бы промт всем остальным, и заметили бы это по
    чужим роликам.
    """
    saved = await client.put("/api/my-prompts/plan", json={"text": "мой план"})
    assert saved.status_code == 200
    body = saved.json()
    assert body["text"] == "мой план"
    assert body["source"] == "tenant"
    assert body["overridden"] is True

    # Системный уровень при этом пуст: клиент его не трогал.
    from app.services.prompt_store import PromptScope, resolve

    assert resolve("plan", "default", PromptScope()) is None


async def test_another_tenant_does_not_see_my_edit(env) -> None:
    """Промт соседа — это его промт, а не общий."""
    client = env["client"]
    await client.put("/api/my-prompts/plan", json={"text": "мой план"})
    other = await client.get("/api/my-prompts/plan", headers=env["neighbour"].auth)
    assert other.json()["text"] != "мой план"
    assert other.json()["overridden"] is False


async def test_project_edit_beats_my_own(client) -> None:
    """«Как в этом ролике» перебивает «как хочу я» — иначе уровень бессмыслен."""
    await client.put("/api/my-prompts/plan", json={"text": "мой обычный"})
    await client.put("/api/my-prompts/plan", json={"text": "для ролика", "project_id": 3})

    mine = (await client.get("/api/my-prompts/plan")).json()
    for_project = (await client.get("/api/my-prompts/plan?project_id=3")).json()
    assert mine["text"] == "мой обычный" and mine["source"] == "tenant"
    assert for_project["text"] == "для ролика" and for_project["source"] == "project"


async def test_reset_returns_to_what_was_before(client) -> None:
    """«Вернуть как было» убирает свою правку, а не чужую."""
    await client.put("/api/my-prompts/plan", json={"text": "мой план"})
    after = (await client.delete("/api/my-prompts/plan")).json()
    assert after["overridden"] is False
    assert after["text"] != "мой план"


async def test_listing_says_where_each_prompt_comes_from(client) -> None:
    """Уровень видно в списке: «правлю, а не меняется» задаётся первым."""
    await client.put("/api/my-prompts/script", json={"text": "мой сценарий"})
    rows = (await client.get("/api/my-prompts")).json()
    by_step = {r["step_code"]: r for r in rows}
    assert by_step["script"]["overridden"] is True
    assert by_step["script"]["source_title"] == "мои"
    assert by_step["plan"]["overridden"] is False


async def test_unknown_step_is_a_404(client) -> None:
    """У шага без мастер-промта редактировать нечего."""
    assert (await client.get("/api/my-prompts/assemble")).status_code == 404
    assert (await client.put("/api/my-prompts/assemble", json={"text": "x"})).status_code == 404


async def test_platform_prompt_handles_stay_closed_to_tenants(client) -> None:
    """Свой редактор открыт, платформенный — нет.

    `/api/prompts` правит промты платформы сразу для всех; открыть его
    клиенту значит дать ему поменять чужие ролики.
    """
    assert (await client.get("/api/prompts")).status_code == 404
    assert (await client.get("/api/prompt-files")).status_code == 404
    assert (await client.get("/api/my-prompts")).status_code == 200
