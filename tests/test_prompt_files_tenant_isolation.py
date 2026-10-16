"""Тесты строгой изоляции промтов между арендаторами в /api/prompt-files.

Проверяем:
1. Участник (member / tenant A) может загружать, сохранять, скачивать и переименовывать свои промты.
2. Промты участника A изолированы: участник B их не видит и не может прочитать/удалить.
3. Системный дефолт видят оба, но правка дефолта участником A влияет только на него.
"""

from __future__ import annotations

import io

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

    engine, factory = await ah.make_engine(tmp_path / "prompt_iso.db")
    ah.bind_identity_session(monkeypatch, factory)

    async def _gen():
        async with factory() as s:
            yield s

    app = create_app()
    app.dependency_overrides[get_session] = _gen

    user_a = await ah.make_account(factory, email="alice@studio.local")
    user_b = await ah.make_account(factory, email="bob@studio.local")

    async with (
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client_a,
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client_b,
    ):
        client_a.headers["Authorization"] = f"Bearer {user_a.token}"
        client_b.headers["Authorization"] = f"Bearer {user_b.token}"
        yield {
            "client_a": client_a,
            "client_b": client_b,
            "user_a": user_a,
            "user_b": user_b,
        }

    prompt_store.reset_cache()
    await engine.dispose()


async def test_member_can_list_and_save_prompt(env) -> None:
    client_a = env["client_a"]
    client_b = env["client_b"]

    # 1. Оба участника могут получить список промтов шага plan
    res_a = await client_a.get("/api/prompt-files/plan")
    assert res_a.status_code == 200
    names_a = [f["name"] for f in res_a.json()]
    assert "default" in names_a

    # 2. Участник A сохраняет правку в свой default
    save_res = await client_a.put(
        "/api/prompt-files/plan/default",
        json={"content": "Кастомный план Алисы для Fallout"},
    )
    assert save_res.status_code == 200

    # 3. Участник A видит свою правку
    content_a = (await client_a.get("/api/prompt-files/plan/default/content")).json()
    assert content_a["content"] == "Кастомный план Алисы для Fallout"

    # 4. Участник B видит базовый (не тронутый Алисой) промт
    content_b = (await client_b.get("/api/prompt-files/plan/default/content")).json()
    assert content_b["content"] != "Кастомный план Алисы для Fallout"


async def test_uploaded_prompt_is_isolated_to_tenant(env) -> None:
    client_a = env["client_a"]
    client_b = env["client_b"]

    # 1. Участник A загружает файл fallout_custom.md
    file_bytes = b"# Fallout Special Prompt\nKill Deathclaw with X-01"
    files = {"file": ("fallout_custom.md", io.BytesIO(file_bytes), "text/markdown")}
    upload_res = await client_a.post("/api/prompt-files/plan/upload", files=files)
    assert upload_res.status_code == 200
    assert upload_res.json()["name"] == "fallout_custom"

    # 2. Участник A видит fallout_custom в списке
    list_a = (await client_a.get("/api/prompt-files/plan")).json()
    assert any(f["name"] == "fallout_custom" for f in list_a)

    # 3. Участник B НЕ видит fallout_custom в своем списке!
    list_b = (await client_b.get("/api/prompt-files/plan")).json()
    assert not any(f["name"] == "fallout_custom" for f in list_b)

    # 4. Участник B не может прочитать контент чужого файла
    res_b_content = await client_b.get("/api/prompt-files/plan/fallout_custom/content")
    assert res_b_content.status_code == 404

    # 5. Участник B не может удалить чужой файл
    del_b = await client_b.delete("/api/prompt-files/plan/fallout_custom")
    assert del_b.status_code == 404

    # 6. Участник A может скачать свой файл
    down_a = await client_a.get("/api/prompt-files/plan/fallout_custom/download")
    assert down_a.status_code == 200
    assert b"Kill Deathclaw with X-01" in down_a.content

    # 7. Участник A переименовывает свой файл
    rename_res = await client_a.patch(
        "/api/prompt-files/plan/fallout_custom/rename",
        json={"new_name": "fallout_v2"},
    )
    assert rename_res.status_code == 200
    assert rename_res.json()["name"] == "fallout_v2"

    # 8. Старое имя исчезло, новое появилось у A
    list_a_after = (await client_a.get("/api/prompt-files/plan")).json()
    assert not any(f["name"] == "fallout_custom" for f in list_a_after)
    assert any(f["name"] == "fallout_v2" for f in list_a_after)

    # 9. Участник A удаляет свой файл
    del_a = await client_a.delete("/api/prompt-files/plan/fallout_v2")
    assert del_a.status_code == 200
    list_a_deleted = (await client_a.get("/api/prompt-files/plan")).json()
    assert not any(f["name"] == "fallout_v2" for f in list_a_deleted)
