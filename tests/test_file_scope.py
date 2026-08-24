"""Файлы чужого арендатора: изоляция там, где row-level security бессильна.

Политика закрывает базу. Медиа лежат на диске, `data_dir` в SaaS общий на
всех, и ручка `GET /api/files?path=…` отдавала что угодно под ним. Это не
обход политики, а путь мимо неё: клиент с совершенно законным токеном,
узнав чужой слаг, забирал чужой ролик целиком.

Проверка «путь под data_dir» от этого не спасает — она про path traversal, а
не про принадлежность. Здесь проверяется вторая половина.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base, Project, ProjectStatus
from app.services.file_scope import ForeignFile, assert_readable, project_slug_of
from app.settings import settings
from app.web.api import create_app
from app.web.deps import get_session


def test_layout_is_read_by_contract_not_by_substring(tmp_path) -> None:
    """Слаг берётся по объявленной раскладке, а не поиском в середине пути.

    Совпадение по подстроке однажды пропустило бы `videos/чужой/…/мой/…` как
    свой: имя своего проекта встречается в пути, и проверка сказала бы «да».
    """
    data = tmp_path / "data"
    for rel in (
        "videos/my-film/frames/01.png",
        "batches/mass/sub/child/clip.mp4",
        "batches/mass/topics.xlsx",
        "library/shared.json",
    ):
        (data / rel).parent.mkdir(parents=True, exist_ok=True)
        (data / rel).write_text("x")

    assert project_slug_of(data / "videos/my-film/frames/01.png", data) == "my-film"
    assert project_slug_of(data / "batches/mass/sub/child/clip.mp4", data) == "child"
    # Папка самого массового проектом не является — у неё свой владелец.
    assert project_slug_of(data / "batches/mass/topics.xlsx", data) is None
    assert project_slug_of(data / "library/shared.json", data) is None
    # Путь вне data_dir не принадлежит никому, а не «корню».
    assert project_slug_of(Path("/etc/passwd"), data) is None


@pytest_asyncio.fixture
async def db(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "allow_unisolated_tenants", True)
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'scope.db'}", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


async def test_owner_mode_does_not_get_in_the_way(db, tmp_path) -> None:
    """Без арендатора проверка выключена: data_dir принадлежит одному человеку."""
    path = tmp_path / "data" / "videos" / "any" / "x.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x")
    async with db() as s:
        await assert_readable(s, path)  # не бросает


async def test_foreign_project_file_is_refused(db, tmp_path) -> None:
    """Главное: слаг чужого проекта не открывает файл.

    На SQLite политики нет, поэтому «чужой» здесь — тот, которого нет в
    базе вовсе. Механика проверки та же: путь раскладывается до слага, слаг
    ищется в `projects`, не нашёлся — файла для клиента не существует. На
    живом Postgres чужую строку скроет уже RLS.
    """
    from app.services.tenant import tenant_scope

    mine = tmp_path / "data" / "videos" / "mine" / "clip.mp4"
    theirs = tmp_path / "data" / "videos" / "theirs" / "clip.mp4"
    for p in (mine, theirs):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x")

    tenant = str(uuid.uuid4())
    async with db() as s:
        s.add(Project(slug="mine", topic="моё", tenant_id=tenant, status=ProjectStatus.new))
        await s.commit()

    with tenant_scope(tenant):
        async with db() as s:
            await assert_readable(s, mine)
            with pytest.raises(ForeignFile, match="theirs"):
                await assert_readable(s, theirs)


async def test_ownerless_paths_are_closed_to_tenants(db, tmp_path) -> None:
    """Библиотека и журналы не принадлежат никому — значит и никому не видны.

    Отдать их «раз владельца нет» значило бы открыть общий каталог всем
    арендаторам сразу.
    """
    from app.services.tenant import tenant_scope

    shared = tmp_path / "data" / "library" / "x.json"
    shared.parent.mkdir(parents=True, exist_ok=True)
    shared.write_text("{}")

    with tenant_scope(str(uuid.uuid4())):
        async with db() as s:
            with pytest.raises(ForeignFile):
                await assert_readable(s, shared)


async def test_http_answers_404_not_403(db, tmp_path, monkeypatch) -> None:
    """Чужой файл отвечает «нет такого», а не «есть, но нельзя».

    403 подтверждает, что слаг угадан верно, — то есть отдаёт сведения о
    чужом проекте вместо самого файла.
    """
    from tests import accounts_harness as ah

    ah.configure(monkeypatch)
    ah.bind_identity_session(monkeypatch, db)
    account = await ah.make_account(db, email="client@studio.local")

    theirs = tmp_path / "data" / "videos" / "someone-else" / "clip.mp4"
    theirs.parent.mkdir(parents=True, exist_ok=True)
    theirs.write_text("x")

    async def _gen():
        async with db() as s:
            yield s

    app = create_app()
    app.dependency_overrides[get_session] = _gen
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        res = await c.get("/api/files", params={"path": str(theirs)}, headers=account.auth)
    assert res.status_code == 404, res.status_code
    assert "not found" in res.text
