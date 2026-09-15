"""Ручки /api/fleet, которые пишут в БД.

Перенос форка заказчика заменил в них голый ``session.commit()`` на
``commit_with_retry`` — повтор на sqlite «database is locked». Роутер был не
покрыт вовсе, поэтому здесь проверяется именно доход до строки коммита: после
каждого запроса состояние перечитывается ОТДЕЛЬНОЙ сессией, а не из ответа.
Ответ мог бы показать объект из незакоммиченной транзакции — перечитанная
строка не может.

Сеть не трогаем: всё, что ходит на другую станцию (`ping_agent`,
`agent_get_bytes`, импорт bundle), подменено на месте в модуле роутера.
"""

from __future__ import annotations

import platform

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

import app.db as app_db
from app.models import FleetNode, FleetNodeStatus, Project, ProjectStatus
from app.settings import settings
from app.web.api import create_app
from app.web.routers import fleet as fleet_router

REMOTE_NAME = "agent-остров"
REMOTE_URL = "http://10.0.0.77:8765"


@pytest_asyncio.fixture
async def client(monkeypatch):
    """Приложение поверх изолированной БД из conftest.

    Роутер флота ходит в БД через ``app.db.session_scope``, а он берёт
    ``SessionLocal`` из глобалей ``app.db`` в момент вызова — autouse-фикстура
    conftest уже увела их на временный файл. Поэтому подменять зависимость
    ``get_session`` тут нечего: её роутер не использует.
    """
    # Очередь монтажа в тестах не должна ничего запускать сама.
    monkeypatch.setattr(settings, "fleet_montage_hub", False)
    monkeypatch.setattr(settings, "fleet_agent_token", "")
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def _make_project(slug: str = "fleet-proj", status: ProjectStatus = ProjectStatus.music_ready) -> int:
    async with app_db.SessionLocal() as s:
        project = Project(slug=slug, topic="тема", status=status, meta={})
        s.add(project)
        await s.commit()
        return project.id


async def _fetch_node(node_id: int) -> FleetNode | None:
    async with app_db.SessionLocal() as s:
        return await s.get(FleetNode, node_id)


async def _fetch_project(project_id: int) -> Project | None:
    async with app_db.SessionLocal() as s:
        return await s.get(Project, project_id)


def _remote_payload(**over) -> dict:
    body = {"name": REMOTE_NAME, "base_url": REMOTE_URL + "/", "token": "t0k", "role": "agent"}
    body.update(over)
    return body


async def _create_remote_node(client: AsyncClient, **over) -> int:
    res = await client.post("/api/fleet/nodes", json=_remote_payload(**over))
    assert res.status_code == 200, res.text
    return int(res.json()["id"])


# ── registry ────────────────────────────────────────────────────────────────


async def test_create_node_persists_and_normalizes_base_url(client) -> None:
    node_id = await _create_remote_node(client)

    stored = await _fetch_node(node_id)
    assert stored is not None, "строка не дожила до отдельной сессии — коммита не было"
    assert stored.base_url == REMOTE_URL, "хвостовой слэш обязан отрезаться при записи"
    assert stored.status == FleetNodeStatus.offline
    assert stored.role == "agent"

    listed = (await client.get("/api/fleet/nodes")).json()
    assert [n["name"] for n in listed] == [REMOTE_NAME]


async def test_create_node_rejects_duplicate_name(client) -> None:
    await _create_remote_node(client)
    res = await client.post("/api/fleet/nodes", json=_remote_payload())
    assert res.status_code == 409
    async with app_db.SessionLocal() as s:
        rows = (await s.execute(select(FleetNode))).scalars().all()
    assert len(rows) == 1, "отбитая по имени станция всё-таки записалась"


async def test_delete_node_removes_the_row(client) -> None:
    node_id = await _create_remote_node(client)
    res = await client.delete(f"/api/fleet/nodes/{node_id}")
    assert res.status_code == 200 and res.json() == {"ok": True}
    assert await _fetch_node(node_id) is None


async def test_delete_unknown_node_is_404(client) -> None:
    assert (await client.delete("/api/fleet/nodes/4242")).status_code == 404


# ── heartbeat ───────────────────────────────────────────────────────────────


async def test_register_creates_node_when_unknown(client) -> None:
    res = await client.post(
        "/api/fleet/register",
        json={"name": REMOTE_NAME, "base_url": REMOTE_URL + "/", "hostname": "ост-1", "role": "agent"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "online"

    stored = await _fetch_node(int(body["id"]))
    assert stored is not None
    assert stored.base_url == REMOTE_URL
    assert stored.status == FleetNodeStatus.online
    assert stored.last_seen is not None


async def test_register_updates_the_existing_node(client) -> None:
    node_id = await _create_remote_node(client)
    res = await client.post(
        "/api/fleet/register",
        json={
            "name": REMOTE_NAME,
            "base_url": "http://10.0.0.99:9000",
            "hostname": "переехал",
            "role": "worker",
            "is_main": True,
        },
    )
    assert res.status_code == 200, res.text
    assert int(res.json()["id"]) == node_id, "heartbeat завёл вторую строку вместо обновления"

    stored = await _fetch_node(node_id)
    assert stored is not None
    assert stored.base_url == "http://10.0.0.99:9000"
    assert stored.hostname == "переехал"
    assert stored.role == "worker"
    assert stored.is_main is True
    assert stored.status == FleetNodeStatus.online


async def test_register_demands_token_when_one_is_configured(client, monkeypatch) -> None:
    monkeypatch.setattr(settings, "fleet_agent_token", "секрет")
    res = await client.post("/api/fleet/register", json={"name": REMOTE_NAME, "base_url": REMOTE_URL})
    assert res.status_code == 401
    res = await client.post(
        "/api/fleet/register",
        json={"name": REMOTE_NAME, "base_url": REMOTE_URL},
        headers={"Authorization": "Bearer wrong-token"},
    )
    assert res.status_code == 403


# ── sync ────────────────────────────────────────────────────────────────────


async def test_sync_local_node_writes_hostname_and_version(client, monkeypatch) -> None:
    """Ветка «станция — это мы»: ходить некуда, данные берём у себя."""
    monkeypatch.setattr(settings, "fleet_is_main", True)
    node_id = await _create_remote_node(client, name="главный", is_main=True)

    res = await client.post(f"/api/fleet/nodes/{node_id}/sync")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["ok"] is True and body["info"]["local"] is True

    stored = await _fetch_node(node_id)
    assert stored is not None
    assert stored.status == FleetNodeStatus.online
    assert stored.hostname == platform.node()
    assert stored.last_seen is not None


@pytest.mark.parametrize(
    ("info", "expected_status"),
    [
        ({"hostname": "ост-1", "studio_version": "v42"}, FleetNodeStatus.online),
        (None, FleetNodeStatus.offline),
    ],
)
async def test_sync_remote_node_records_the_agent_answer(client, monkeypatch, info, expected_status) -> None:
    monkeypatch.setattr(settings, "fleet_is_main", False)
    monkeypatch.setattr(settings, "fleet_node_name", "не-эта-станция")
    node_id = await _create_remote_node(client)

    async def _ping(base_url: str, token: str | None) -> dict | None:
        assert base_url == REMOTE_URL
        assert token == "t0k"
        return info

    monkeypatch.setattr(fleet_router, "ping_agent", _ping)

    res = await client.post(f"/api/fleet/nodes/{node_id}/sync")
    assert res.status_code == 200, res.text
    assert res.json()["ok"] is bool(info)

    stored = await _fetch_node(node_id)
    assert stored is not None
    assert stored.status == expected_status
    if info:
        assert stored.hostname == "ост-1"
        assert stored.pipeline_version == "v42"


async def test_sync_unknown_node_is_404(client) -> None:
    assert (await client.post("/api/fleet/nodes/4242/sync")).status_code == 404


# ── pull-to-main ────────────────────────────────────────────────────────────


async def test_pull_to_main_локально_ставит_проект_в_очередь(client, monkeypatch) -> None:
    monkeypatch.setattr(settings, "fleet_is_main", True)
    monkeypatch.setattr(settings, "fleet_montage_hub", False)
    node_id = await _create_remote_node(client, name="главный", is_main=True)
    project_id = await _make_project("pull-local")

    res = await client.post(
        f"/api/fleet/nodes/{node_id}/projects/{project_id}/pull-to-main",
        json={"run_assemble": True},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body == {
        "ok": True,
        "project_id": project_id,
        "slug": "pull-local",
        "local": True,
        "queued": True,
    }

    stored = await _fetch_project(project_id)
    assert stored is not None
    meta = stored.meta or {}
    assert meta["montage_ready"] is True
    assert meta["fleet_local_montage"] is True
    assert meta[fleet_router.META_ENQUEUED] is True, "флаг очереди не доехал до коммита"


async def test_pull_to_main_локально_без_сборки_только_метит(client, monkeypatch) -> None:
    monkeypatch.setattr(settings, "fleet_is_main", True)
    node_id = await _create_remote_node(client, name="главный", is_main=True)
    project_id = await _make_project("pull-local-mark")

    res = await client.post(
        f"/api/fleet/nodes/{node_id}/projects/{project_id}/pull-to-main",
        json={"run_assemble": False},
    )
    assert res.status_code == 200, res.text
    assert res.json()["queued"] is False

    stored = await _fetch_project(project_id)
    assert stored is not None
    assert (stored.meta or {}).get("montage_ready") is True
    assert fleet_router.META_ENQUEUED not in (stored.meta or {})


async def test_pull_to_main_локально_404_на_чужой_проект(client, monkeypatch) -> None:
    monkeypatch.setattr(settings, "fleet_is_main", True)
    node_id = await _create_remote_node(client, name="главный", is_main=True)
    res = await client.post(
        f"/api/fleet/nodes/{node_id}/projects/4242/pull-to-main", json={"run_assemble": False}
    )
    assert res.status_code == 404


async def test_pull_to_main_с_агента_импортирует_bundle(client, monkeypatch) -> None:
    """Удалённая ветка: транспорт и распаковка подменены, проверяется роутер."""
    monkeypatch.setattr(settings, "fleet_is_main", False)
    monkeypatch.setattr(settings, "fleet_node_name", "не-эта-станция")
    monkeypatch.setattr(settings, "fleet_montage_hub", False)
    node_id = await _create_remote_node(client)
    project_id = await _make_project("pull-remote")

    async def _get_bytes(base_url, token, path, timeout_sec=600):
        assert base_url == REMOTE_URL
        assert path.endswith("/export-bundle")
        return b"tar.gz"

    async def _import(session, blob, *, run_assemble=False):
        assert blob == b"tar.gz"
        return await session.get(Project, project_id)

    monkeypatch.setattr(fleet_router, "agent_get_bytes", _get_bytes)
    monkeypatch.setattr(fleet_router.bundle_svc, "import_project_bundle", _import)

    res = await client.post(
        f"/api/fleet/nodes/{node_id}/projects/777/pull-to-main", json={"run_assemble": True}
    )
    assert res.status_code == 200, res.text
    assert res.json() == {"ok": True, "project_id": project_id, "slug": "pull-remote", "queued": True}

    stored = await _fetch_project(project_id)
    assert stored is not None
    meta = stored.meta or {}
    assert meta["fleet_source_node"] == REMOTE_NAME
    assert meta["fleet_source_project_id"] == 777
    assert meta[fleet_router.META_ENQUEUED] is True


async def test_pull_to_main_с_пустым_bundle_это_502(client, monkeypatch) -> None:
    monkeypatch.setattr(settings, "fleet_is_main", False)
    monkeypatch.setattr(settings, "fleet_node_name", "не-эта-станция")
    node_id = await _create_remote_node(client)

    async def _get_bytes(base_url, token, path, timeout_sec=600):
        return b""

    monkeypatch.setattr(fleet_router, "agent_get_bytes", _get_bytes)
    res = await client.post(
        f"/api/fleet/nodes/{node_id}/projects/1/pull-to-main", json={"run_assemble": False}
    )
    assert res.status_code == 502


async def test_pull_to_main_прокидывает_ошибку_агента(client, monkeypatch) -> None:
    monkeypatch.setattr(settings, "fleet_is_main", False)
    monkeypatch.setattr(settings, "fleet_node_name", "не-эта-станция")
    node_id = await _create_remote_node(client)

    async def _get_bytes(base_url, token, path, timeout_sec=600):
        raise fleet_router.FleetAgentError(503, "станция спит")

    monkeypatch.setattr(fleet_router, "agent_get_bytes", _get_bytes)
    res = await client.post(
        f"/api/fleet/nodes/{node_id}/projects/1/pull-to-main", json={"run_assemble": False}
    )
    assert res.status_code == 503
    assert res.json()["detail"] == "станция спит"


# ── local agent ─────────────────────────────────────────────────────────────


async def test_local_mark_montage_ready_writes_the_flag(client) -> None:
    project_id = await _make_project("mark-ready", status=ProjectStatus.new)
    res = await client.post(f"/api/fleet/local/projects/{project_id}/mark-montage-ready")
    assert res.status_code == 200, res.text
    assert res.json() == {"ok": True, "project_id": project_id, "slug": "mark-ready"}

    stored = await _fetch_project(project_id)
    assert stored is not None
    assert (stored.meta or {}).get("montage_ready") is True


async def test_local_mark_montage_ready_404_on_unknown_project(client) -> None:
    res = await client.post("/api/fleet/local/projects/4242/mark-montage-ready")
    assert res.status_code == 404
