"""Граф ролика: диф, операции, план сброса, предложение → применение.

Проверяются рамки, а не рисование: что правка графа на живом проекте
обязана показать, что сгорит, что применяется только проверенный граф, и что
выключенный узел виден и планировщику, и стадиям.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base, Project, ProjectStatus
from app.orchestrator.default_graph import default_graph
from app.services.project_graph import (
    GraphError,
    ProjectGraph,
    apply_graph_ops,
    apply_project_graph,
    apply_proposal,
    describe_graph,
    discard_proposal,
    edge_kind,
    graph_diff,
    layout_graph,
    load_project_graph,
    node_step_code,
    normalize_graph,
    proposal_from_meta,
    propose_project_graph,
    reset_plan,
    stage_nodes,
)
from app.settings import settings


@pytest.fixture
async def db(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "allow_unisolated_tenants", True)
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'graph.db'}", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr("app.db.SessionLocal", factory)
    yield factory
    await engine.dispose()


def _project(status: ProjectStatus = ProjectStatus.new, **kw) -> Project:
    p = Project(slug="g", title="g", topic="тема", status=status, meta={})
    for k, v in kw.items():
        setattr(p, k, v)
    return p


def _mini():
    nodes = [
        {"id": "n_topic", "type": "topic", "data": {}},
        {"id": "n_plan", "type": "plan", "data": {}},
        {"id": "n_script", "type": "script", "data": {}},
        {"id": "n_split", "type": "split", "data": {}},
    ]
    edges = [
        {"id": "e0", "source": "n_topic", "target": "n_plan"},
        {"id": "e1", "source": "n_plan", "target": "n_script"},
        {"id": "e2", "source": "n_script", "target": "n_split"},
    ]
    return nodes, edges


# ── диф ─────────────────────────────────────────────────────────────────


def test_diff_ignores_positions_and_sees_everything_else():
    nodes, edges = _mini()
    moved = [dict(n, position={"x": 999, "y": 1}) for n in nodes]
    assert graph_diff(nodes, edges, moved, edges).empty

    changed = [dict(n) for n in nodes]
    changed[1] = {**changed[1], "data": {"modelId": "gpt-5.6-sol"}}
    changed.append({"id": "n_check", "type": "hitl_gate", "data": {}})
    new_edges = edges[:-1] + [{"id": "e3", "source": "n_script", "target": "n_check"}]
    d = graph_diff(nodes, edges, changed, new_edges)
    assert [n["id"] for n in d.added_nodes] == ["n_check"]
    assert d.changed_nodes[0]["id"] == "n_plan" and "modelId" in d.changed_nodes[0]["changes"]
    assert {(e["source"], e["target"]) for e in d.removed_edges} == {("n_script", "n_split")}
    assert {(e["source"], e["target"]) for e in d.added_edges} == {("n_script", "n_check")}
    assert "+1 узл." in d.summary() and "~1 узл." in d.summary()


# ── план сброса ─────────────────────────────────────────────────────────


def test_reset_plan_burns_only_done_steps_and_takes_the_cone():
    """Правка узла, который проект уже прошёл, сжигает его и всё ниже."""
    nodes, edges = _mini()
    p = _project(ProjectStatus.frames_ready)  # plan, script, split сделаны
    changed = [dict(n) for n in nodes]
    changed[2] = {**changed[2], "data": {"modelId": "other"}}
    d = graph_diff(nodes, edges, changed, edges)
    plan = reset_plan(p, nodes, changed, d)
    assert plan.first_step == "script"
    assert "script" in plan.steps and "split" in plan.steps and "plan" not in plan.steps
    assert "n_script" not in plan.reasons and any("изменён" in r for r in plan.reasons.values())


def test_reset_plan_is_empty_when_nothing_done_yet_or_only_cosmetics():
    nodes, edges = _mini()
    p_new = _project(ProjectStatus.new)
    changed = [dict(n) for n in nodes]
    changed[1] = {**changed[1], "data": {"modelId": "x"}}
    assert reset_plan(p_new, nodes, changed, graph_diff(nodes, edges, changed, edges)).first_step is None

    p_done = _project(ProjectStatus.frames_ready)
    relabel = [dict(n) for n in nodes]
    relabel[1] = {**relabel[1], "data": {"label": "Новый сценарий"}}
    assert reset_plan(p_done, nodes, relabel, graph_diff(nodes, edges, relabel, edges)).first_step is None


def test_added_node_in_passed_region_burns_from_there():
    nodes, edges = _mini()
    p = _project(ProjectStatus.frames_ready)
    new_nodes, new_edges, _ = apply_graph_ops(
        nodes, edges, [{"op": "add_node", "type": "script", "after": "n_plan"}]
    )
    d = graph_diff(nodes, edges, new_nodes, new_edges)
    plan = reset_plan(p, nodes, new_nodes, d)
    assert plan.first_step == "script"


# ── операции ────────────────────────────────────────────────────────────


def test_add_node_after_rewires_the_chain():
    nodes, edges = _mini()
    nodes[2]["position"] = {"x": 300, "y": 200}
    new_nodes, new_edges, applied = apply_graph_ops(
        nodes, edges, [{"op": "add_node", "type": "hitl_gate", "after": "n_script", "label": "Проверка"}]
    )
    added = next(n for n in new_nodes if n["type"] == "hitl_gate")
    pairs = {(e["source"], e["target"]) for e in new_edges}
    assert ("n_script", added["id"]) in pairs and (added["id"], "n_split") in pairs
    assert ("n_script", "n_split") not in pairs
    assert added["data"]["label"] == "Проверка"
    assert "position" in added  # поставлен рядом с якорем, не в (0,0)
    assert applied and applied[0].startswith("add_node")


def test_remove_node_bridges_neighbours():
    nodes, edges = _mini()
    new_nodes, new_edges, _ = apply_graph_ops(nodes, edges, [{"op": "remove_node", "id": "n_script"}])
    assert {n["id"] for n in new_nodes} == {"n_topic", "n_plan", "n_split"}
    assert ("n_plan", "n_split") in {(e["source"], e["target"]) for e in new_edges}


def test_ops_are_all_or_nothing_and_speak_human():
    nodes, edges = _mini()
    with pytest.raises(GraphError) as exc:
        apply_graph_ops(
            nodes,
            edges,
            [{"op": "set_node", "id": "n_plan", "disabled": True}, {"op": "remove_node", "id": "nope"}],
        )
    assert "nope" in str(exc.value) and "n_plan" in str(exc.value)
    with pytest.raises(GraphError):
        apply_graph_ops(nodes, edges, [{"op": "add_node", "type": "teleport"}])
    with pytest.raises(GraphError):
        apply_graph_ops(
            nodes, edges, [{"op": "set_edge_kind", "source": "n_plan", "target": "n_script", "kind": "maybe"}]
        )


def test_set_node_and_edge_kind():
    nodes, edges = _mini()
    new_nodes, new_edges, _ = apply_graph_ops(
        nodes,
        edges,
        [
            {"op": "set_node", "id": "n_plan", "disabled": True, "model_id": "gpt-5.6-sol"},
            {"op": "set_edge_kind", "source": "n_plan", "target": "n_script", "kind": "pass"},
            {"op": "disconnect", "source": "n_script", "target": "n_split"},
            {"op": "connect", "source": "n_plan", "target": "n_split", "kind": "fail"},
        ],
    )
    plan = next(n for n in new_nodes if n["id"] == "n_plan")
    assert plan["data"] == {"disabled": True, "modelId": "gpt-5.6-sol"}
    kinds = {(e["source"], e["target"]): edge_kind(e) for e in new_edges}
    assert kinds[("n_plan", "n_script")] == "pass"
    assert kinds[("n_plan", "n_split")] == "fail"
    assert ("n_script", "n_split") not in kinds


# ── раскладка и нормализация ────────────────────────────────────────────


def test_layout_places_missing_positions_by_layers_and_keeps_manual_ones():
    nodes, edges = _mini()
    nodes[0]["position"] = {"x": 5, "y": 5}
    laid = layout_graph(nodes, edges)
    assert laid[0]["position"] == {"x": 5, "y": 5}
    xs = [laid[i]["position"]["x"] for i in (1, 2, 3)]
    assert xs == sorted(xs) and len(set(xs)) == 3


def test_normalize_rejects_unknown_types_and_cycles():
    nodes, edges = _mini()
    with pytest.raises(GraphError):
        normalize_graph([{"id": "a", "type": "warp"}], [])
    _, _, check = normalize_graph(nodes, edges + [{"id": "back", "source": "n_split", "target": "n_plan"}])
    assert not check["valid"] and any("цикл" in e for e in check["errors"])


def test_node_step_code_knows_enrich_slots_and_scene_agents():
    assert node_step_code({"id": "x", "type": "images", "data": {}}) == "img"
    assert node_step_code({"id": "x", "type": "excel_gpt", "data": {"slotIndex": 2}}) == "enrich_2"
    assert node_step_code({"id": "x", "type": "hitl_gate", "data": {}}) is None


# ── предложение → применение ────────────────────────────────────────────


async def test_propose_stores_diff_and_apply_writes_graph_and_disabled(db):
    async with db() as s:
        p = _project(ProjectStatus.new)
        s.add(p)
        await s.commit()

        current = await load_project_graph(s, p)
        assert current.source == "default" and not current.owned

        nodes, edges, _ = apply_graph_ops(
            current.nodes, current.edges, [{"op": "set_node", "id": "n_music", "disabled": True}]
        )
        proposal = await propose_project_graph(s, p, nodes, edges, reason="без музыки")
        await s.commit()
        assert proposal_from_meta(p)["id"] == proposal["id"]
        assert proposal["diff"]["changed_nodes"][0]["id"] == "n_music"
        assert proposal["reset"]["first_step"] is None  # проект новый — гореть нечему

        with pytest.raises(GraphError):
            await apply_proposal(s, p, "stale-id")

        result = await apply_proposal(s, p, proposal["id"])
        await s.commit()
        assert result["diff"]["summary"] == "~1 узл."
        assert proposal_from_meta(p) is None
        assert p.meta["disabled_nodes"] == ["n_music"]
        assert (await load_project_graph(s, p)).owned

        # Планировщик видит выключенный узел по id из графа, не по имени.
        from app.services.disabled_nodes import disabled_node_types

        assert disabled_node_types(p) == {"music"}


async def test_apply_refuses_invalid_graph_and_keeps_current(db):
    async with db() as s:
        p = _project(ProjectStatus.new)
        s.add(p)
        await s.commit()
        nodes, edges = _mini()
        with pytest.raises(GraphError):
            await apply_project_graph(s, p, nodes, edges + [{"source": "n_split", "target": "n_plan"}])
        assert "canvas_graph" not in (p.meta or {})


async def test_apply_can_shrink_the_graph_hard_when_asked(db):
    """Анти-wipe эвристика PATCH здесь не действует: применение осознанное."""
    async with db() as s:
        p = _project(ProjectStatus.new)
        s.add(p)
        await s.commit()
        nodes, edges = _mini()
        result = await apply_project_graph(s, p, nodes, edges)
        await s.commit()
        assert len(p.meta["canvas_graph"]["nodes"]) == 4
        assert result["reset_done"] is False


async def test_discard_proposal(db):
    async with db() as s:
        p = _project()
        s.add(p)
        await s.commit()
        nodes, edges = _mini()
        await propose_project_graph(s, p, nodes, edges)
        assert discard_proposal(p) is True
        assert proposal_from_meta(p) is None
        assert discard_proposal(p) is False


# ── представления ───────────────────────────────────────────────────────


def test_describe_and_stage_nodes():
    nodes, edges = default_graph()
    p = _project(ProjectStatus.script_ready)
    graph = ProjectGraph(nodes=nodes, edges=edges, source="default")
    brief = describe_graph(p, graph)
    by_id = {n["id"]: n for n in brief["nodes"]}
    assert by_id["n_plan"]["state"] == "done" and by_id["n_split"]["state"] == "pending"
    assert by_id["n_plan"]["stage"] == "plan" and by_id["n_images"]["stage"] == "images"
    assert "position" not in by_id["n_plan"]

    grouped = stage_nodes(graph, {})
    assert [n["type"] for n in grouped["images"]] == ["image_prompts", "images"]
    assert grouped["final"][0]["type"] == "audio"
