"""Граф ролика: ветки, которые основной тест не задевает.

Источники графа (снимок прогона, штатная схема), синонимы видов связи,
операции с ошибками, сброс при применении, стадии на паузе и вход стадии по
графу. Каждый случай — либо реальный путь из интерфейса/чата, либо отказ,
текст которого читает модель.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base, Project, ProjectStatus, Workflow, WorkflowRun
from app.services import pipeline_stages as ps
from app.services.project_graph import (
    GraphError,
    ProjectGraph,
    apply_graph_ops,
    apply_project_graph,
    describe_graph,
    edge_kind,
    graph_diff,
    load_project_graph,
    node_states,
    node_step_code,
    normalize_graph,
    project_rank,
    reset_plan,
    reset_project_graph_to_default,
    step_is_done,
)
from app.settings import settings


@pytest.fixture
async def db(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "allow_unisolated_tenants", True)
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'edges.db'}", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr("app.db.SessionLocal", factory)
    yield factory
    await engine.dispose()


def _project(status: ProjectStatus = ProjectStatus.new, meta: dict | None = None) -> Project:
    return Project(slug="e", title="e", topic="тема", status=status, meta=meta or {})


def _mini():
    nodes = [
        {"id": "n_topic", "type": "topic", "data": {}},
        {"id": "n_plan", "type": "plan", "data": {}, "position": {"x": 0, "y": 0}},
        {"id": "n_script", "type": "script", "data": {}, "position": {"x": 290, "y": 0}},
        {"id": "n_split", "type": "split", "data": {}, "position": {"x": 580, "y": 0}},
    ]
    edges = [
        {"id": "e0", "source": "n_topic", "target": "n_plan"},
        {"id": "e1", "source": "n_plan", "target": "n_script"},
        {"id": "e2", "source": "n_script", "target": "n_split"},
    ]
    return nodes, edges


# ── мелочи чтения ───────────────────────────────────────────────────────


def test_edge_kind_understands_legacy_synonyms():
    assert edge_kind({"data": {"kind": "ok"}}) == "pass"
    assert edge_kind({"kind": "не ок"}) == "fail"
    assert edge_kind({"data": {"kind": "feed"}}) == "after"
    assert edge_kind({"data": {"kind": "teleport"}}) == "after"
    assert edge_kind({}) == "after"


def test_node_step_code_for_gpt_node_without_slot():
    assert node_step_code({"id": "x", "type": "excel_gpt", "data": {}}) == "enrich_1"
    assert node_step_code({"id": "x", "type": "excel_gpt", "data": {"slotOverflow": True}}) == "excel_gpt"


def test_project_rank_uses_last_linear_status_when_paused():
    paused = _project(ProjectStatus.paused, {"last_linear_status": "script_ready"})
    assert project_rank(paused) == ps.status_rank(ProjectStatus.script_ready)
    assert project_rank(_project(ProjectStatus.failed, {"last_linear_status": "garbage"})) == -1
    assert project_rank(_project(ProjectStatus.paused)) == -1
    assert step_is_done(paused, None) is False
    assert step_is_done(paused, "not_a_step") is False
    assert step_is_done(paused, "plan") is True


# ── диф и план сброса ───────────────────────────────────────────────────


def test_diff_reports_type_change_and_edge_kind_change():
    nodes, edges = _mini()
    changed = [dict(n) for n in nodes]
    changed[3] = {**changed[3], "type": "hitl_gate"}
    new_edges = [dict(e) for e in edges]
    new_edges[1] = {**new_edges[1], "data": {"kind": "pass"}}
    d = graph_diff(nodes, edges, changed, new_edges)
    assert d.changed_nodes[0]["changes"] == ["type"]
    assert d.changed_edges[0]["was"] == "after" and d.changed_edges[0]["kind"] == "pass"
    assert "~1 связ." in d.summary()


def test_reset_plan_for_removed_node_and_rewired_edge():
    nodes, edges = _mini()
    p = _project(ProjectStatus.frames_ready)
    without = [n for n in nodes if n["id"] != "n_split"]
    fewer = [e for e in edges if e["target"] != "n_split"]
    plan = reset_plan(p, nodes, without, graph_diff(nodes, edges, without, fewer))
    assert plan.first_step == "split" and any("удалён" in r for r in plan.reasons.values())

    rewired = [dict(e) for e in edges]
    rewired[2] = {**rewired[2], "data": {"kind": "gate"}}
    plan2 = reset_plan(p, nodes, nodes, graph_diff(nodes, edges, nodes, rewired))
    assert plan2.first_step == "split" and any("связи" in r for r in plan2.reasons.values())


# ── операции: отказы и редкие ветки ─────────────────────────────────────


def test_ops_refuse_nonsense_with_readable_reasons():
    nodes, edges = _mini()
    cases = [
        [{"op": "connect", "source": "n_plan", "target": "n_plan"}],
        [{"op": "connect", "source": "n_plan", "target": "n_split", "kind": "sideways"}],
        ["not an op"],
        [{"op": "disconnect", "source": "n_plan", "target": "n_split"}],
        [{"op": "set_edge_kind", "source": "n_plan", "target": "n_split", "kind": "pass"}],
        [{"op": "fly"}],
    ]
    for ops in cases:
        with pytest.raises(GraphError):
            apply_graph_ops(nodes, edges, ops)


def test_connect_existing_edge_changes_kind_and_add_node_before():
    nodes, edges = _mini()
    new_nodes, new_edges, _ = apply_graph_ops(
        nodes,
        edges,
        [
            {"op": "connect", "source": "n_plan", "target": "n_script", "kind": "gate"},
            {"op": "add_node", "type": "hitl_gate", "before": "n_split", "model_id": "m-1"},
            {"op": "set_node", "id": "n_plan", "label": "План", "model_id": "", "data": {"note": 1}},
        ],
    )
    kinds = {(e["source"], e["target"]): edge_kind(e) for e in new_edges}
    assert kinds[("n_plan", "n_script")] == "gate"
    gate = next(n for n in new_nodes if n["type"] == "hitl_gate")
    assert kinds[("n_script", gate["id"])] == "after" and kinds[(gate["id"], "n_split")] == "after"
    assert gate["data"]["modelId"] == "m-1" and gate["position"]["x"] < 580
    plan = next(n for n in new_nodes if n["id"] == "n_plan")
    assert plan["data"] == {"label": "План", "note": 1}


def test_normalize_drops_duplicate_edges_and_rejects_missing_id():
    nodes, edges = _mini()
    doubled = edges + [{"id": "dup", "source": "n_plan", "target": "n_script"}, "junk"]
    clean_nodes, clean_edges, check = normalize_graph(nodes, doubled)
    assert len(clean_edges) == 3 and check["valid"]
    with pytest.raises(GraphError):
        normalize_graph([{"type": "plan"}], [])


def test_normalize_softens_feedback_loop_through_check_node():
    """Петля «проверка → назад» превращается в «не ок», а не в ошибку."""
    nodes = [
        {"id": "a", "type": "plan", "data": {}},
        {"id": "chk", "type": "excel_gpt", "data": {}},
        {"id": "b", "type": "script", "data": {}},
    ]
    edges = [
        {"id": "e1", "source": "a", "target": "chk"},
        {"id": "e2", "source": "chk", "target": "b"},
        {"id": "e3", "source": "chk", "target": "a"},
    ]
    _, clean_edges, check = normalize_graph(nodes, edges)
    assert check["valid"]
    kinds = {(e["source"], e["target"]): edge_kind(e) for e in clean_edges}
    assert kinds[("chk", "a")] == "fail"


# ── источники графа и применение со сбросом ─────────────────────────────


async def test_graph_comes_from_run_snapshot_then_default_workflow(db):
    nodes, edges = _mini()
    async with db() as s:
        wf = Workflow(name="штатная", nodes=nodes, edges=edges, is_default=True, version=1)
        s.add(wf)
        p = _project()
        s.add(p)
        await s.flush()
        assert (await load_project_graph(s, p)).source == "workflow"

        s.add(
            WorkflowRun(
                workflow_id=wf.id, project_id=p.id, nodes_snapshot=nodes[:2], edges_snapshot=edges[:1]
            )
        )
        await s.flush()
        g = await load_project_graph(s, p)
        assert g.source == "run" and len(g.nodes) == 2

        result = await reset_project_graph_to_default(s, p)
        await s.commit()
        assert len(result["nodes"]) == 4 and (await load_project_graph(s, p)).source == "canvas"


async def test_apply_with_reset_burns_done_steps(db):
    nodes, edges = _mini()
    async with db() as s:
        p = _project(ProjectStatus.script_ready)
        p.general_plan = "план"
        p.script_text = "текст"
        s.add(p)
        await s.commit()
        await apply_project_graph(s, p, nodes, edges, reset=False)
        changed = [dict(n) for n in nodes]
        changed[1] = {**changed[1], "data": {"modelId": "other"}}
        result = await apply_project_graph(s, p, changed, edges, reset=True)
        await s.commit()
        assert result["reset"]["first_step"] == "plan"
        assert result["reset_done"] is True
        assert p.status is not ProjectStatus.script_ready


def test_node_states_degrades_to_empty_and_describe_shows_flags(monkeypatch):
    nodes, edges = _mini()
    nodes[1]["data"] = {"disabled": True, "modelId": "m-9"}
    graph = ProjectGraph(nodes=nodes, edges=edges, source="canvas")
    p = _project(ProjectStatus.new)
    brief = {n["id"]: n for n in describe_graph(p, graph)["nodes"]}
    assert brief["n_plan"]["disabled"] is True and brief["n_plan"]["model_id"] == "m-9"

    from app.orchestrator.graph import planner

    def boom(self, project):
        raise RuntimeError("no")

    monkeypatch.setattr(planner.WorkflowGraph, "derived_node_states", boom)
    assert node_states(p, graph) == {}


# ── стадии поверх графа ─────────────────────────────────────────────────


def test_entry_step_follows_first_enabled_node_and_paused_rank():
    nodes, edges = _mini()
    nodes[1]["data"] = {"disabled": True}  # сценарий выключен
    graph = ProjectGraph(nodes=nodes, edges=edges, source="canvas")
    plan_stage = ps.STAGE_BY_ID["plan"]
    script_stage = ps.STAGE_BY_ID["script"]
    p = _project(ProjectStatus.new)
    assert ps.entry_step_code(p, script_stage, graph) == "script"
    # У стадии нет включённых узлов — вход остаётся прежним, стадия skipped.
    assert ps.entry_step_code(p, plan_stage, graph) == "plan"
    assert ps.stage_is_skipped(graph, "plan") and not ps.stage_is_skipped(graph, "script")
    assert ps.entry_step_code(p, ps.STAGE_BY_ID["cast"], graph) in ("objects", "scene_d")

    paused = _project(ProjectStatus.paused, {"last_linear_status": "script_ready"})
    states = {st.stage.id: st.state for st in ps.stage_states(paused, graph)}
    assert states["plan"] == "done" and states["script"] == "done" and states["frames"] == "paused"
    junk = _project(ProjectStatus.paused, {"last_linear_status": "junk"})
    assert ps.stage_states(junk)[0].state == "paused"


async def test_apply_survives_failed_reset(db, monkeypatch):
    """Сброс упал — граф всё равно применён, а отказ виден в ответе, не в 500."""
    nodes, edges = _mini()

    async def broken(session, project, step_code):
        return {"error": f"нет такого шага {step_code}"}

    monkeypatch.setattr("app.services.reset_step.reset_step", broken)
    async with db() as s:
        p = _project(ProjectStatus.script_ready)
        s.add(p)
        await s.commit()
        await apply_project_graph(s, p, nodes, edges, reset=False)
        changed = [dict(n) for n in nodes]
        changed[1] = {**changed[1], "data": {"modelId": "other"}}
        result = await apply_project_graph(s, p, changed, edges, reset=True)
        assert result["reset_done"] is False and result["reset_summary"]["error"]
        assert (await load_project_graph(s, p)).nodes[1]["data"]["modelId"] == "other"
