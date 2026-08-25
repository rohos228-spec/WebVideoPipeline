"""`/prompts/{step}/resolve` отвечает и без проекта.

Конструктор конвейера правит промт УЗЛА, а не ролика: там нет и не должно быть
проекта, подставлять туда чужой — враньё. Раньше `project_id` был обязательным
параметром, и такой вызов возвращал 422; в интерфейсе это выглядело как «промт
не читается», без намёка на причину.

Проверка держит обе ветки. Без проекта — общий уровень («какой вариант возьмёт
шаг, если переопределений нет»). С проектом — прежнее поведение, включая 404 на
несуществующий: тихо подменять его общим уровнем нельзя, иначе опечатка в
адресе выглядела бы как рабочий ответ.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def app_client(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.web.routers import prompt_files

    app = FastAPI()
    app.include_router(prompt_files.router, prefix="/api")

    async def _no_session():
        return None

    app.dependency_overrides[prompt_files.get_session] = _no_session
    return TestClient(app)


def test_resolve_without_project_answers(app_client) -> None:
    """Общий уровень: ответ есть, имя варианта непустое."""
    r = app_client.get("/api/prompt-files/plan/resolve")

    assert r.status_code == 200, f"ожидали общий уровень, получили {r.status_code}: {r.text[:200]}"
    body = r.json()
    assert body["name"], "вариант промта должен быть назван"
    assert body["source_label"], "источник должен быть подписан по-человечески"


def test_unknown_step_is_still_404(app_client) -> None:
    """Несуществующий шаг остаётся ошибкой, а не «общим уровнем»."""
    r = app_client.get("/api/prompt-files/такого-шага-нет/resolve")
    assert r.status_code == 404
