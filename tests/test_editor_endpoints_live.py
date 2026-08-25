"""Ручки редактора отвечают на настоящих запросах, а не только существуют.

Сверка путей (`test_frontend_api_paths.py`) отвечает лишь на вопрос «адрес
есть?». Этого мало: 2026-08-26 клиент был подключён к соседнему роутеру
`/api/prompts` вместо `/api/prompt-files`, адрес существовал, и проверка
прошла бы. Здесь запросы делаются по-настоящему — с базой, файлами и разбором
ответа.

Покрыт весь круг, которым пользуется человек: создать промт, поправить,
прочитать обратно, увидеть версию в истории, откатиться. Плюс конструктор:
проверка графа на годном и на битом, создание штатной схемы.

Файл промта создаётся в рабочей библиотеке и удаляется в `finally` — иначе
проба оставляла бы мусор в `prompts/`, который потом уедет в базу при импорте.
"""

from __future__ import annotations

import shutil

import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base
from app.services.prompt_library import prompt_path
from app.web.routers import prompt_files, workflows

STEP = "plan"
NAME = "проба-редактора-тест"


@pytest_asyncio.fixture
async def client(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'editor.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    app = FastAPI()
    app.include_router(prompt_files.router, prefix="/api")
    app.include_router(workflows.router, prefix="/api")

    async def _session():
        async with factory() as s:
            yield s
            await s.commit()

    app.dependency_overrides[prompt_files.get_session] = _session
    app.dependency_overrides[workflows.get_session] = _session

    target = prompt_path(STEP, NAME)
    try:
        yield TestClient(app)
    finally:
        # Файл — и его историю: `.history/<имя>/` остаётся после unlink и
        # копится в рабочей библиотеке от прогона к прогону.
        if target.exists():
            target.unlink()
        shutil.rmtree(target.parent / ".history" / NAME, ignore_errors=True)
        await engine.dispose()


def test_prompt_round_trip(client) -> None:
    """Создать → поправить → прочитать → версия в истории → откат."""
    r = client.put(f"/api/prompt-files/{STEP}/{NAME}", json={"content": "первая редакция"})
    assert r.status_code == 200, r.text
    assert r.json()["name"] == NAME

    r = client.put(f"/api/prompt-files/{STEP}/{NAME}", json={"content": "вторая редакция"})
    assert r.status_code == 200, r.text

    r = client.get(f"/api/prompt-files/{STEP}/{NAME}/content")
    assert r.status_code == 200
    assert r.json()["content"] == "вторая редакция"

    r = client.get(f"/api/prompt-files/{STEP}/{NAME}/history")
    assert r.status_code == 200
    versions = r.json()
    assert versions, "правка не оставила версии — откатываться будет некуда"

    r = client.post(f"/api/prompt-files/{STEP}/{NAME}/history/{versions[-1]['id']}/restore")
    assert r.status_code == 200
    assert r.json()["content"] == "первая редакция", "откат вернул не ту редакцию"


def test_resolve_answers_with_and_without_project(client) -> None:
    """Общий уровень и проектный — разные ветки одной ручки."""
    r = client.get(f"/api/prompt-files/{STEP}/resolve")
    assert r.status_code == 200, r.text
    assert r.json()["name"]

    # Несуществующий проект — честная ошибка, а не тихий общий уровень:
    # иначе опечатка в адресе выглядела бы как рабочий ответ.
    r = client.get(f"/api/prompt-files/{STEP}/resolve?project_id=999999")
    assert r.status_code == 404


def test_catalog_is_not_empty(client) -> None:
    """Палитра конструктора получает типы узлов."""
    r = client.get("/api/workflows/catalog")
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["nodes"]) > 20
    assert body["kinds"]


def test_graph_validation_separates_good_from_broken(client) -> None:
    """Проверка графа отвечает по существу, а не «всегда ок»."""
    from app.orchestrator.default_graph import default_graph

    nodes, edges = default_graph()

    r = client.post("/api/workflows/validate", json={"name": "проба", "nodes": nodes, "edges": edges})
    assert r.status_code == 200, r.text
    assert r.json()["valid"] is True, f"штатная схема считается битой: {r.json().get('errors')}"

    broken = edges + [{"id": "x", "source": nodes[0]["id"], "target": "нет-такой-ноды"}]
    r = client.post("/api/workflows/validate", json={"name": "проба", "nodes": nodes, "edges": broken})
    assert r.status_code == 200
    body = r.json()
    assert body["valid"] is False
    assert body["errors"], "битый граф без единого сообщения — чинить будет нечего"


def test_default_workflow_can_be_created(client) -> None:
    """«Вернуть штатную» — путь назад, если конструктором всё сломали."""
    r = client.post("/api/workflows/default/reset")
    assert r.status_code == 200, r.text
    assert len(r.json()["nodes"]) > 10

    r = client.get("/api/workflows")
    assert r.status_code == 200
    assert len(r.json()) == 1
