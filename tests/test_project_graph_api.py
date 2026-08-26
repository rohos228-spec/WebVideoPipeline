"""Ручки графа ролика и стадий поверх графа — через приложение целиком.

Сервис проверен отдельно (`test_project_graph.py`); здесь — что роутер
подключён, что ответ имеет ту форму, которую читает фронт (`web/src/lib/
types.ts`), и что выключенный на схеме узел виден стадии как `skipped`.
"""

from __future__ import annotations

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base, Project, ProjectStatus
from app.settings import settings
from app.web.api import create_app
from app.web.deps import get_session


@pytest_asyncio.fixture
async def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "allow_unisolated_tenants", True)
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'graph-api.db'}", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        s.add(Project(slug="graph-api", topic="тема", status=ProjectStatus.new, meta={}))
        await s.commit()

    async def _gen():
        async with factory() as s:
            yield s

    app = create_app()
    app.dependency_overrides[get_session] = _gen
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        c.factory = factory  # type: ignore[attr-defined]
        yield c
    await engine.dispose()


async def test_graph_roundtrip_and_stages_reflect_it(client) -> None:
    res = await client.get("/api/projects/1/graph")
    assert res.status_code == 200, res.text
    g = res.json()
    assert g["source"] == "default" and g["proposal"] is None
    assert {"nodes", "edges", "states", "prices", "catalog", "models"} <= set(g)
    assert any(c["type"] == "images" and c["stage"] == "images" for c in g["catalog"])

    # Выключаем весь сценарий: стадия «Сценарий» обязана стать skipped.
    nodes = [
        dict(n, data={**(n.get("data") or {}), "disabled": True}) if n["type"] == "plan" else n
        for n in g["nodes"]
    ]
    diff = await client.post("/api/projects/1/graph/diff", json={"nodes": nodes, "edges": g["edges"]})
    assert diff.status_code == 200
    assert diff.json()["diff"]["summary"] == "~1 узл."
    assert diff.json()["reset"]["first_step"] is None

    put = await client.put("/api/projects/1/graph", json={"nodes": nodes, "edges": g["edges"], "reset": True})
    assert put.status_code == 200, put.text
    assert put.json()["reset_done"] is False

    again = (await client.get("/api/projects/1/graph")).json()
    assert again["source"] == "canvas"
    assert again["states"]["n_plan"] == "skipped"

    stages = (await client.get("/api/projects/1/stages")).json()
    by_id = {s["id"]: s for s in stages["stages"]}
    assert by_id["plan"]["state"] == "skipped"
    assert by_id["script"]["state"] == "ready"  # следующая занимает место выключенной
    assert stages["graph_source"] == "canvas"
    assert any(n["id"] == "n_plan" and n["disabled"] for n in by_id["plan"]["nodes"])

    run = await client.post("/api/projects/1/stages/plan/run")
    assert run.status_code == 400 and "выключена" in run.json()["detail"]


async def test_invalid_graph_is_rejected_with_reasons(client) -> None:
    g = (await client.get("/api/projects/1/graph")).json()
    bad_edges = g["edges"] + [{"source": "n_publish", "target": "n_images"}]
    res = await client.put("/api/projects/1/graph", json={"nodes": g["nodes"], "edges": bad_edges})
    assert res.status_code == 400
    assert any("цикл" in e for e in res.json()["detail"]["graph"])
    assert (await client.get("/api/projects/1/graph")).json()["source"] == "default"


async def test_reset_to_default_and_proposal_endpoints(client) -> None:
    g = (await client.get("/api/projects/1/graph")).json()
    nodes = [n for n in g["nodes"] if n["type"] != "music"]
    edges = [e for e in g["edges"] if "n_music" not in (e["source"], e["target"])]
    edges.append({"source": "n_audio", "target": "n_sfx_plan"})
    assert (
        await client.put("/api/projects/1/graph", json={"nodes": nodes, "edges": edges})
    ).status_code == 200
    assert not any(n["type"] == "music" for n in (await client.get("/api/projects/1/graph")).json()["nodes"])

    back = await client.post("/api/projects/1/graph/reset")
    assert back.status_code == 200 and any(n["type"] == "music" for n in back.json()["nodes"])

    # Предложения без агента нет — применить нечего, отклонить безопасно.
    res = await client.post("/api/projects/1/graph/proposal/apply", json={"proposal_id": "nope"})
    assert res.status_code == 400
    assert (await client.delete("/api/projects/1/graph/proposal")).status_code == 204


async def test_missing_project_brief_and_broken_catalog(client, monkeypatch) -> None:
    assert (await client.get("/api/projects/77/graph")).status_code == 404
    brief = await client.get("/api/projects/1/graph/brief")
    assert brief.status_code == 200 and any(n["id"] == "n_plan" for n in brief.json()["nodes"])

    def boom():
        raise RuntimeError("no catalog")

    monkeypatch.setattr("app.services.vibecode_catalog.models_for_channel", boom)
    g = (await client.get("/api/projects/1/graph")).json()
    assert g["models"] == {"text": [], "image": [], "video": []}

    bad = await client.post(
        "/api/projects/1/graph/diff", json={"nodes": [{"id": "x", "type": "warp"}], "edges": []}
    )
    assert bad.status_code == 400 and bad.json()["detail"]["graph"]


async def test_agent_proposal_is_applied_through_the_button_endpoint(client) -> None:
    from app.services.studio_agent import call_tool

    async with client.factory() as s:  # type: ignore[attr-defined]
        card = await call_tool(
            s, "editGraph", {"project_id": 1, "ops": [{"op": "set_node", "id": "n_music", "disabled": True}]}
        )
    res = await client.post("/api/projects/1/graph/proposal/apply", json={"proposal_id": card["proposal_id"]})
    assert res.status_code == 200, res.text
    assert res.json()["diff"]["summary"] == "~1 узл."
    assert (await client.get("/api/projects/1/graph")).json()["proposal"] is None


async def test_stage_run_and_stop_go_through_the_graph(client) -> None:
    run = await client.post("/api/projects/1/stages/plan/run")
    assert run.status_code == 200, run.text
    assert run.json()["step"] == "plan" and run.json()["project"]["status"] == "planning"
    stop = await client.post("/api/projects/1/stages/stop")
    assert stop.status_code == 200
    assert (await client.post("/api/projects/1/stages/nope/run")).status_code == 404


async def test_models_of_unknown_kind_are_not_offered(client, monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.vibecode_catalog.models_for_channel",
        lambda *a, **k: [
            {"id": "a", "kind": "audio", "label": "A"},
            {"id": "t", "kind": "text", "label": "T"},
        ],
    )
    g = (await client.get("/api/projects/1/graph")).json()
    assert [m["id"] for m in g["models"]["text"]] == ["t"] and g["models"]["image"] == []
