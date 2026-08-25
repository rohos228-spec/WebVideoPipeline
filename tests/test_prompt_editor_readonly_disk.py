"""Редактор промтов работает на диске только для чтения — и шаг видит правку.

Две поломки, найденные первым живым открытием редактора на сервере
2026-08-26.

**Диск `:ro`.** `prompts/` смонтирован только для чтения
(`deploy/studio/docker-compose.yml`). Роутер жил на диске: `step_dir` делал
`mkdir` — 500 на любой шаг без папки; запись — `OSError`; история —
`OSError`. Весь редактор был мёртв на сервере.

**Сохранение не доходило до шага.** Это хуже и не зависит от диска.
`read_prompt` идёт «база, потом диск» через `prompt_store`, кэш поднят при
старте. Роутер писал файл и `MasterPrompt`, но не `prompt_store` — редактор
говорил «сохранено», шаг читал старый текст из кэша.

Здесь диск подменяется каталогом, куда писать нельзя, и проверяется весь
круг: список, запись, чтение той же дорогой, что у шага, история, откат.
"""

from __future__ import annotations

import os
import stat

import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base
from app.services import prompt_library, prompt_store
from app.services.prompt_library import read_prompt
from app.services.prompt_store import PromptScope
from app.web.routers import prompt_files

STEP = "plan"


@pytest_asyncio.fixture
async def ro_client(tmp_path, monkeypatch):
    """Клиент роутера поверх базы и диска, куда писать нельзя."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'ro.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    ro_root = tmp_path / "prompts-ro"
    ro_root.mkdir()
    # Папка шага есть, но файлов в ней нет и создать нельзя — как на сервере
    # для нового шага. Права снимаем и с корня, и с папки.
    (ro_root / prompt_library.STEP_FOLDERS[STEP]).mkdir()
    for d in (ro_root / prompt_library.STEP_FOLDERS[STEP], ro_root):
        os.chmod(d, stat.S_IRUSR | stat.S_IXUSR)

    monkeypatch.setattr(prompt_library, "PROMPTS_ROOT", ro_root)
    prompt_store.reset_cache()
    # В базе — системный промт, как после import_from_disk на первом старте.
    async with factory() as s:
        await prompt_store.save(s, STEP, "default", "системный текст", scope=PromptScope())
        await s.commit()

    app = FastAPI()
    app.include_router(prompt_files.router, prefix="/api")

    async def _session():
        async with factory() as s:
            yield s
            await s.commit()

    app.dependency_overrides[prompt_files.get_session] = _session
    try:
        yield TestClient(app)
    finally:
        for d in (ro_root, ro_root / prompt_library.STEP_FOLDERS[STEP]):
            os.chmod(d, stat.S_IRWXU)
        prompt_store.reset_cache()
        await engine.dispose()


def _skip_if_root():
    if os.geteuid() == 0:
        pytest.skip("под root права на каталог не действуют — проверка бессмысленна")


def test_disk_is_really_read_only(ro_client) -> None:
    """Страховка самого теста: если сюда можно писать, дальше всё зелёное впустую."""
    _skip_if_root()
    assert prompt_library.prompts_writable() is False


def test_list_shows_db_only_prompt(ro_client) -> None:
    """Промт, которого нет на диске, виден в списке — иначе «не заведено»."""
    _skip_if_root()
    r = ro_client.get(f"/api/prompt-files/{STEP}")
    assert r.status_code == 200, r.text
    assert [x["name"] for x in r.json()] == ["default"]


def test_step_without_folder_is_not_a_500(ro_client) -> None:
    """Ровно та ошибка с сервера: `mkdir` на `:ro` ронял список."""
    _skip_if_root()
    r = ro_client.get("/api/prompt-files/cast")
    assert r.status_code == 200, r.text


def test_save_reaches_the_step(ro_client) -> None:
    """Главное: после сохранения шаг читает НОВЫЙ текст."""
    _skip_if_root()
    r = ro_client.put(f"/api/prompt-files/{STEP}/default", json={"content": "новый текст"})
    assert r.status_code == 200, r.text

    assert read_prompt(STEP, "default") == "новый текст", (
        "редактор сказал «сохранено», а шаг читает старое — та самая поломка"
    )
    r = ro_client.get(f"/api/prompt-files/{STEP}/default/content")
    assert r.json()["content"] == "новый текст"


def test_history_and_restore_without_disk(ro_client) -> None:
    """Версии живут в базе: откат работает там, где `.history/` нет."""
    _skip_if_root()
    ro_client.put(f"/api/prompt-files/{STEP}/default", json={"content": "первая"})
    ro_client.put(f"/api/prompt-files/{STEP}/default", json={"content": "вторая"})

    r = ro_client.get(f"/api/prompt-files/{STEP}/default/history")
    assert r.status_code == 200, r.text
    versions = r.json()
    assert versions, "правки не оставили версий — откатываться некуда"
    first = next(v for v in versions if v["id"].startswith("db-"))

    r = ro_client.get(f"/api/prompt-files/{STEP}/default/history/{first['id']}/content")
    assert r.status_code == 200
    older = r.json()["content"]
    assert older in ("первая", "системный текст")

    r = ro_client.post(f"/api/prompt-files/{STEP}/default/history/{first['id']}/restore")
    assert r.status_code == 200, r.text
    assert read_prompt(STEP, "default") == older, "откат вернул файл, а шаг читает прежнее"


def test_rename_is_an_honest_409(ro_client) -> None:
    """Переименование — операция над файлом; без диска говорим «недоступно»."""
    _skip_if_root()
    r = ro_client.patch(f"/api/prompt-files/{STEP}/default/rename", json={"new_name": "другое"})
    assert r.status_code == 409
    assert "только для чтения" in r.json()["detail"]
