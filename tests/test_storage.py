"""Объектное хранилище: изоляция ключом и отдача мимо приложения.

Файл на диске узла в SaaS ломается трижды: виден только записавшему узлу,
упирается в его диск на всех арендаторов сразу, и каждая отдача занимает
воркер на время скачивания (`docs/SAAS-PIVOT.md` §9.2).

S3 живой здесь не поднимается — бакета нет, а поднимать minio ради проверки
подписи значит проверять minio. Проверяются те две вещи, в которых ошибиться
можно и без сети: как строится ключ (это граница арендаторов) и что отдача
переключается на подписанную ссылку, когда объект опубликован.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Artifact, ArtifactKind, Base, Project, ProjectStatus
from app.services.storage import LocalStorage, StorageError, safe_path, tenant_key
from app.settings import settings


def test_key_cannot_leave_its_prefix() -> None:
    """Главное свойство: арендатор не назовёт ключ вне своего префикса.

    Это poka-yoke, а не проверка: `..` вырезается до сборки ключа, поэтому
    выйти наружу нечем. Требование «не забыть проверить `..`» здесь заменено
    на «`..` не существует».
    """
    alice, bob = str(uuid.uuid4()), str(uuid.uuid4())
    assert tenant_key(alice, "projects/film/a.png") == f"studio/{alice}/projects/film/a.png"

    # Классические попытки выйти: точки, ведущие слэши, обратные слэши.
    escaped = tenant_key(alice, f"../{bob}/secret.mp4")
    assert escaped == f"studio/{alice}/{bob}/secret.mp4"
    assert f"studio/{bob}" not in escaped
    assert tenant_key(alice, "///a//b///c.png") == f"studio/{alice}/a/b/c.png"
    assert tenant_key(alice, "a\\b\\c.png") == f"studio/{alice}/a/b/c.png"


def test_control_characters_and_empty_paths_are_refused() -> None:
    """Пустой путь — обращение к корню чужого префикса, а не «к папке»."""
    with pytest.raises(StorageError):
        safe_path("")
    with pytest.raises(StorageError):
        safe_path("../..")
    with pytest.raises(StorageError):
        safe_path("a\0b")


def test_owner_has_a_prefix_too() -> None:
    """Отсутствие арендатора — это режим, а не дыра: у владельца свой префикс."""
    assert tenant_key(None, "projects/x/a.png").startswith("studio/owner/")


async def test_local_storage_round_trip(tmp_path) -> None:
    """Локальное хранилище — рабочий режим владельца, а не заглушка."""
    storage = LocalStorage(tmp_path / "objects")
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"video")

    key = tenant_key(None, "projects/film/clip.mp4")
    assert await storage.put_file(key, source) == key
    assert await storage.exists(key)
    assert await storage.get_bytes(key) == b"video"
    assert storage.url(key).startswith("/api/files?")

    await storage.delete(key)
    assert not await storage.exists(key)
    # Удаление отсутствующего — не ошибка: повтор не должен падать.
    await storage.delete(key)


async def test_local_storage_uses_the_same_sanitizer(tmp_path) -> None:
    """Два хранилища с разными правилами ключа разъехались бы молча.

    Файл записан под одним ключом, ищется под другим — и не находится, а
    ошибки при этом нет ни одной.
    """
    storage = LocalStorage(tmp_path / "objects")
    inside = storage.path_for(tenant_key("a" * 8, "../../etc/passwd"))
    assert (tmp_path / "objects") in inside.parents


@pytest_asyncio.fixture
async def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'st.db'}", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    media = tmp_path / "data" / "videos" / "st" / "final.mp4"
    media.parent.mkdir(parents=True, exist_ok=True)
    media.write_bytes(b"video")

    async with factory() as s:
        p = Project(slug="st", topic="хранилище", status=ProjectStatus.new)
        s.add(p)
        await s.flush()
        s.add(
            Artifact(
                project_id=p.id,
                kind=ArtifactKind.final_video,
                uuid="art-1",
                path=str(media),
            )
        )
        await s.commit()

    from app.web.api import create_app
    from app.web.deps import get_session

    async def _gen():
        async with factory() as s:
            yield s

    app = create_app()
    app.dependency_overrides[get_session] = _gen
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        c.factory = factory  # type: ignore[attr-defined]
        yield c
    await engine.dispose()


async def test_unpublished_artifact_is_served_from_disk(client) -> None:
    """Режим владельца: поведение остаётся ровно прежним."""
    res = await client.get("/api/artifacts/art-1/file")
    assert res.status_code == 200
    assert res.content == b"video"


async def test_published_artifact_redirects_to_a_signed_link(client, monkeypatch) -> None:
    """Опубликованный отдаётся мимо приложения.

    Ролик — десятки мегабайт, и каждый просмотр через FastAPI занимает воркер
    на всё время скачивания. Перенаправление 307, а не 302: метод обязан
    сохраниться, а ссылка живёт по сроку и кэшировать её нельзя.
    """
    from app.services import storage as storage_module

    class _Signed:
        enabled = True

        def url(self, key, *, ttl_sec=900, download_name=""):
            return f"https://bucket.example/{key}?signed=1"

    monkeypatch.setattr(storage_module, "get_storage", lambda: _Signed())
    monkeypatch.setattr("app.services.artifact_storage.get_storage", lambda: _Signed())

    async with client.factory() as s:  # type: ignore[attr-defined]
        art = await s.get(Artifact, 1)
        art.storage_key = "studio/owner/projects/st/art-1.mp4"
        await s.commit()

    res = await client.get("/api/artifacts/art-1/file", follow_redirects=False)
    assert res.status_code == 307
    assert res.headers["location"].startswith("https://bucket.example/studio/owner/")


async def test_publishing_is_idempotent_and_survives_a_dead_bucket(client, monkeypatch) -> None:
    """Ошибка публикации не роняет шаг: ролик оплачен, его надо отдать.

    Ключ остаётся пустым, артефакт продолжает отдаваться с диска, а
    следующий шаг попробует опубликовать снова.
    """
    from app.services.artifact_storage import publish_artifact

    class _Broken:
        enabled = True

        async def put_file(self, key, source, *, content_type=""):
            raise StorageError("бакет недоступен")

    monkeypatch.setattr("app.services.artifact_storage.get_storage", lambda: _Broken())
    async with client.factory() as s:  # type: ignore[attr-defined]
        art = await s.get(Artifact, 1)
        assert await publish_artifact(s, art) == ""
        assert art.storage_key == ""

    class _Ok:
        enabled = True
        calls = 0

        async def put_file(self, key, source, *, content_type=""):
            _Ok.calls += 1
            return key

    monkeypatch.setattr("app.services.artifact_storage.get_storage", lambda: _Ok())
    async with client.factory() as s:  # type: ignore[attr-defined]
        art = await s.get(Artifact, 1)
        key = await publish_artifact(s, art)
        await s.commit()
    assert key.startswith("studio/owner/projects/st/art-1")

    async with client.factory() as s:  # type: ignore[attr-defined]
        art = await s.get(Artifact, 1)
        assert await publish_artifact(s, art) == key
    assert _Ok.calls == 1, "повторная публикация стоила бы трафика и ничего не меняла"


async def test_artifact_without_a_file_is_not_an_error(client, monkeypatch) -> None:
    """Запись о файле, которого нет, публиковать нечего — и это не сбой.

    Файл могли удалить вручную или он не доехал с узла; ронять на этом шаг
    значит останавливать конвейер из-за отсутствующей пробы.
    """
    from app.services.artifact_storage import publish_artifact

    async with client.factory() as s:  # type: ignore[attr-defined]
        art = await s.get(Artifact, 1)
        art.path = str(Path(str(art.path)).parent / "нет-такого.mp4")
        assert await publish_artifact(s, art) == ""
