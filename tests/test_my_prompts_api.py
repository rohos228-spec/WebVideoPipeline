"""Редактор промтов клиента: то место, куда он идёт «менять промпт».

Ручки `/api/prompts` и `/api/prompt-files` для этого не годятся: они правят
промты ПЛАТФОРМЫ, одни на всех, и потому в SaaS закрыты. Здесь у клиента свой
вход, пишущий в его область.
"""

from __future__ import annotations

import time
import uuid

import jwt
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base
from app.services import prompt_store
from app.settings import settings
from app.web.api import create_app
from app.web.deps import get_session

SECRET = "s2s-jwt-secret-at-least-32-bytes-long-0123456789"
TENANT = str(uuid.uuid4())


def _token(sub: str = TENANT) -> str:
    return jwt.encode(
        {
            "sub": sub,
            "email": "c@example.com",
            "brand": "multik",
            "iat": int(time.time()),
            "exp": int(time.time()) + 600,
        },
        SECRET,
        algorithm="HS256",
    )


@pytest_asyncio.fixture
async def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "billing_jwt_secret", SECRET)
    monkeypatch.setattr(settings, "studio_brand", "multik")
    monkeypatch.setattr(settings, "allow_unisolated_tenants", True)
    prompt_store.reset_cache()
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'mp.db'}", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _gen():
        async with factory() as s:
            yield s

    app = create_app()
    app.dependency_overrides[get_session] = _gen
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        c.headers["Authorization"] = f"Bearer {_token()}"
        yield c
    prompt_store.reset_cache()
    await engine.dispose()


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


async def test_another_tenant_does_not_see_my_edit(client) -> None:
    """Промт соседа — это его промт, а не общий."""
    await client.put("/api/my-prompts/plan", json={"text": "мой план"})
    other = await client.get(
        "/api/my-prompts/plan", headers={"Authorization": f"Bearer {_token(str(uuid.uuid4()))}"}
    )
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
