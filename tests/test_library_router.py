"""Ручки /api/library — версионируемая библиотека промтов.

Роутер писал `session.commit()`, перенос форка заказчика заменил его на
`commit_with_retry` (повтор на sqlite «database is locked»). Покрытия у файла
не было вовсе, поэтому здесь по ручке на каждую запись, и проверка не «200», а
«строка пережила транзакцию»: состояние перечитывается отдельной сессией и
через GET-ручки.

Диск библиотеки (`data/library/{current,old}`) живёт под `settings.data_dir`,
а его autouse-фикстура `tests/conftest.py` уводит в tmp — репозиторий не
трогается.
"""

from __future__ import annotations

import json

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

import app.db as app_db
from app.models import LibraryConfig, LibraryEvent, LibraryItem, LibraryVersion, Project, ProjectStatus
from app.services import local_library as lib
from app.services import prompt_composer as pc
from app.web.api import create_app
from app.web.deps import get_session


@pytest_asyncio.fixture
async def client():
    """Клиент поверх изолированной БД conftest.

    `app/web/deps.py` берёт `SessionLocal` В МОМЕНТ ИМПОРТА, поэтому подмена
    `app.db.SessionLocal` в conftest до него не доходит: без явного
    `dependency_overrides` роутер писал бы в боевую `data/state.db`.
    """

    async def _gen():
        async with app_db.SessionLocal() as session:
            yield session

    app = create_app()
    app.dependency_overrides[get_session] = _gen
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def step_template(tmp_path, monkeypatch):
    """Шаблон шага в tmp: в `prompts/` репозитория каталога `steps/` нет вовсе.

    Подменяется только `STEPS_ROOT` — `_prompt_roots()` тогда ставит tmp перед
    репозиторным `prompts/`, и мастер-промты шагов (`prompts/04_hero/…`)
    остаются читаемыми оттуда.
    """
    steps_root = tmp_path / "prompts" / "steps"
    (steps_root / "04_hero").mkdir(parents=True)
    (steps_root / "04_hero" / "template.md").write_text(
        "# Шаг 4 — Герой\n\nТема: {{VAR:PROJECT_TOPIC}}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(pc, "STEPS_ROOT", steps_root)
    return steps_root


async def _make_project(slug: str = "lib-proj") -> Project:
    async with app_db.SessionLocal() as s:
        project = Project(
            slug=slug,
            topic="тема ролика",
            status=ProjectStatus.new,
            meta={"origin": "тест"},
            prompt_overrides={"style_profile": "нуар"},
            gpt_text_overrides={"plan": "свой текст"},
        )
        s.add(project)
        await s.commit()
        await s.refresh(project)
        return project


async def _create_item(client: AsyncClient, **over) -> dict:
    payload = {
        "kind": "prompt",
        "key": "prompts/custom/один.md",
        "title": "Один",
        "file_path": "prompts/custom/один.md",
        "content": "первая редакция",
        "message": "создано тестом",
        "meta": {"tag": "тест"},
    }
    payload.update(over)
    res = await client.post("/api/library/items", json=payload)
    assert res.status_code == 200, res.text
    return res.json()


# ── items ───────────────────────────────────────────────────────────────────


async def test_create_item_записывает_версию_и_файл(client) -> None:
    body = await _create_item(client)
    assert body["active_version"] == 1
    assert body["content"] == "первая редакция"

    async with app_db.SessionLocal() as s:
        item = await s.get(LibraryItem, body["id"])
        versions = (
            (await s.execute(select(LibraryVersion).where(LibraryVersion.item_id == body["id"])))
            .scalars()
            .all()
        )
    assert item is not None, "элемент не дожил до отдельной сессии — коммита не было"
    assert item.meta == {"tag": "тест"}
    assert [v.version for v in versions] == [1]
    # Материализованная копия на диске — её читает композитор промтов.
    assert lib.materialized_file_path(item).read_text(encoding="utf-8") == "первая редакция"


async def test_create_item_придумывает_путь_когда_его_не_дали(client) -> None:
    body = await _create_item(client, key=None, file_path=None, kind="style", title="Мой/стиль")
    assert body["file_path"] == "prompts/custom/Мой_стиль.json"
    assert body["key"] == body["file_path"]

    async with app_db.SessionLocal() as s:
        assert await s.get(LibraryItem, body["id"]) is not None


async def test_update_item_поднимает_версию(client) -> None:
    created = await _create_item(client)
    res = await client.put(
        f"/api/library/items/{created['id']}",
        json={"title": "Один (правка)", "content": "вторая редакция", "message": "правка"},
    )
    assert res.status_code == 200, res.text
    assert res.json()["active_version"] == 2
    assert res.json()["content"] == "вторая редакция"

    # Перечитываем через GET — то есть уже из зафиксированной транзакции.
    fresh = (await client.get(f"/api/library/items/{created['id']}")).json()
    assert fresh["content"] == "вторая редакция"
    assert fresh["title"] == "Один (правка)"
    versions = (await client.get(f"/api/library/items/{created['id']}/versions")).json()
    assert [v["version"] for v in versions] == [2, 1]


async def test_update_unknown_item_404(client) -> None:
    res = await client.put("/api/library/items/4242", json={"content": "нет такого"})
    assert res.status_code == 404


async def test_restore_version_возвращает_старое_содержимое(client) -> None:
    created = await _create_item(client)
    await client.put(f"/api/library/items/{created['id']}", json={"content": "вторая редакция"})

    res = await client.post(f"/api/library/items/{created['id']}/restore/1")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["content"] == "первая редакция"
    assert body["active_version"] == 3, "восстановление обязано быть НОВОЙ версией, а не откатом"

    fresh = (await client.get(f"/api/library/items/{created['id']}")).json()
    assert fresh["content"] == "первая редакция"
    async with app_db.SessionLocal() as s:
        kinds = (
            (await s.execute(select(LibraryEvent.event_type).where(LibraryEvent.item_id == created["id"])))
            .scalars()
            .all()
        )
    assert "restored" in kinds, "событие восстановления не доехало до коммита"


async def test_restore_unknown_version_404(client) -> None:
    created = await _create_item(client)
    assert (await client.post(f"/api/library/items/{created['id']}/restore/99")).status_code == 404
    assert (await client.post("/api/library/items/4242/restore/1")).status_code == 404


async def test_download_item_отдаёт_файл_и_пишет_событие(client) -> None:
    created = await _create_item(client)
    res = await client.get(f"/api/library/items/{created['id']}/download")
    assert res.status_code == 200, res.text
    assert res.text == "первая редакция"

    async with app_db.SessionLocal() as s:
        kinds = (
            (await s.execute(select(LibraryEvent.event_type).where(LibraryEvent.item_id == created["id"])))
            .scalars()
            .all()
        )
    assert "downloaded" in kinds, "журнал скачивания не зафиксирован"


async def test_download_404_когда_файла_на_диске_нет(client) -> None:
    created = await _create_item(client)
    async with app_db.SessionLocal() as s:
        item = await s.get(LibraryItem, created["id"])
        assert item is not None
        lib.materialized_file_path(item).unlink()
    res = await client.get(f"/api/library/items/{created['id']}/download")
    assert res.status_code == 404
    assert (await client.get("/api/library/items/4242/download")).status_code == 404


# ── configs ─────────────────────────────────────────────────────────────────


async def test_save_config_из_готового_снимка(client) -> None:
    res = await client.post(
        "/api/library/configs/save",
        json={"name": "ручной", "snapshot": {"prompt_overrides": {"style_profile": "нуар"}}},
    )
    assert res.status_code == 200, res.text
    cfg_id = res.json()["id"]

    async with app_db.SessionLocal() as s:
        cfg = await s.get(LibraryConfig, cfg_id)
    assert cfg is not None, "конфиг не дожил до отдельной сессии"
    assert cfg.name == "ручной"
    assert cfg.meta == {"source": "api"}
    listed = (await client.get("/api/library/configs")).json()
    assert [c["id"] for c in listed] == [cfg_id]


async def test_save_config_снимает_состояние_проекта(client) -> None:
    project = await _make_project()
    res = await client.post("/api/library/configs/save", json={"project_id": project.id})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["project_id"] == project.id
    assert body["snapshot"]["prompt_overrides"] == {"style_profile": "нуар"}
    assert body["snapshot"]["gpt_text_overrides"] == {"plan": "свой текст"}

    async with app_db.SessionLocal() as s:
        assert await s.get(LibraryConfig, body["id"]) is not None


async def test_save_config_без_проекта_и_снимка_это_400(client) -> None:
    res = await client.post("/api/library/configs/save", json={})
    assert res.status_code == 400
    res = await client.post("/api/library/configs/save", json={"project_id": 4242})
    assert res.status_code == 404


async def test_apply_config_переносит_настройки_в_проект(client) -> None:
    source = await _make_project("lib-src")
    target = await _make_project("lib-dst")
    cfg = (await client.post("/api/library/configs/save", json={"project_id": source.id})).json()

    async with app_db.SessionLocal() as s:
        row = await s.get(Project, target.id)
        assert row is not None
        row.prompt_overrides = {}
        row.gpt_text_overrides = {}
        await s.commit()

    res = await client.post(f"/api/library/configs/{cfg['id']}/apply/{target.id}")
    assert res.status_code == 200, res.text
    assert res.json() == {"ok": True, "project_id": target.id, "config_id": cfg["id"]}

    async with app_db.SessionLocal() as s:
        row = await s.get(Project, target.id)
    assert row is not None
    assert row.prompt_overrides == {"style_profile": "нуар"}
    assert row.gpt_text_overrides == {"plan": "свой текст"}


async def test_apply_config_404_на_неизвестные_id(client) -> None:
    project = await _make_project("lib-apply-404")
    assert (await client.post(f"/api/library/configs/4242/apply/{project.id}")).status_code == 404
    assert (await client.post("/api/library/configs/1/apply/4242")).status_code == 404


# ── prompt bundles ──────────────────────────────────────────────────────────


async def test_save_prompt_bundle_с_готовыми_текстами(client) -> None:
    project = await _make_project("lib-bundle")
    res = await client.post(
        "/api/library/prompt-bundles/save",
        json={
            "project_id": project.id,
            "title": "связка-один",
            "step_code": "plan",
            "source_prompt": "исходный промт",
            "processed_prompt": "собранный промт",
            "blocks": [{"kind": "world", "label": "мир", "body": "текст блока"}],
        },
    )
    assert res.status_code == 200, res.text
    items = res.json()["items"]
    assert set(items) == {"manifest", "source", "processed", "blocks"}
    assert len(items["blocks"]) == 1

    async with app_db.SessionLocal() as s:
        source = await s.get(LibraryItem, items["source"]["id"])
        manifest = await s.get(LibraryItem, items["manifest"]["id"])
        version = (
            await s.execute(select(LibraryVersion).where(LibraryVersion.item_id == items["processed"]["id"]))
        ).scalar_one()
    assert source is not None and manifest is not None, "связка не дожила до отдельной сессии"
    assert version.content == "собранный промт"
    assert json.loads(await _active_content(manifest))["step_code"] == "plan"


async def _active_content(item: LibraryItem) -> str:
    async with app_db.SessionLocal() as s:
        version = await lib.get_active_version(s, item)
    assert version is not None
    return version.content


async def test_save_prompt_bundle_собирает_промт_сам(client, step_template) -> None:
    """Без `processed_prompt` ручка обязана собрать текст по step_code сама."""
    project = await _make_project("lib-bundle-auto")
    res = await client.post(
        "/api/library/prompt-bundles/save",
        json={"project_id": project.id, "step_code": "hero", "source_name": "default"},
    )
    assert res.status_code == 200, res.text
    items = res.json()["items"]
    assert (await _source_text(items["source"]["id"])).strip(), (
        "мастер-промт из prompts/04_hero/default.md не подтянулся"
    )
    processed = await _source_text(items["processed"]["id"])
    assert "тема ролика" in processed, "шаблон шага собран без подстановки темы проекта"


async def test_save_prompt_bundle_по_типу_узла(client, step_template) -> None:
    """Ветка node_type: ни step_id, ни step_code — промт по типу узла графа."""
    res = await client.post(
        "/api/library/prompt-bundles/save",
        json={"node_type": "hero", "title": "по-узлу"},
    )
    assert res.status_code == 200, res.text
    items = res.json()["items"]
    assert "Шаг 4 — Герой" in await _source_text(items["processed"]["id"])
    async with app_db.SessionLocal() as s:
        assert await s.get(LibraryItem, items["manifest"]["id"]) is not None


async def _source_text(item_id: int) -> str:
    async with app_db.SessionLocal() as s:
        item = await s.get(LibraryItem, item_id)
        assert item is not None
        version = await lib.get_active_version(s, item)
    assert version is not None
    return version.content


async def test_save_prompt_bundle_без_шага_это_400(client) -> None:
    res = await client.post("/api/library/prompt-bundles/save", json={"title": "пустая"})
    assert res.status_code == 400
    res = await client.post("/api/library/prompt-bundles/save", json={"project_id": 4242})
    assert res.status_code == 404
