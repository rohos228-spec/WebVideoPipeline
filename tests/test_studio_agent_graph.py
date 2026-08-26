"""Агент и граф: предложить можно, применить — только с согласия.

Правило §8.2 не отменено, а уточнено: модель не решает порядок исполнения,
но может предложить форму конвейера. Здесь проверяется, что «предложить» и
«применить» — разные действия, что второе без `confirm` не проходит, и что
сброс шагов тоже спрашивает.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base, Project, ProjectStatus
from app.services.studio_agent import ToolError, call_tool
from app.services.studio_agent.loop import build_system_prompt, collect_turn
from app.settings import settings


@pytest.fixture
async def db(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "allow_unisolated_tenants", True)
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'agent-graph.db'}", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr("app.db.SessionLocal", factory)
    async with factory() as s:
        s.add(Project(slug="agent-graph", topic="агент", status=ProjectStatus.new, meta={}))
        await s.commit()
    yield factory
    await engine.dispose()


def _scripted(*replies: str):
    queue = list(replies)

    async def _ask(prompt, system, history):
        return queue.pop(0) if len(queue) > 1 else queue[0]

    return _ask


def test_prompt_explains_the_graph_rules():
    prompt = build_system_prompt()
    for name in ("showStages", "showGraph", "editGraph", "proposeGraph", "applyGraph", "resetStep"):
        assert name in prompt
    assert "confirm=true" in prompt


async def test_show_graph_and_stages_are_free_to_call(db):
    async with db() as s:
        graph = await call_tool(s, "showGraph", {"project_id": 1})
        assert graph["source"] == "default"
        assert any(n["id"] == "n_plan" and n["state"] == "pending" for n in graph["nodes"])
        assert all("position" not in n for n in graph["nodes"])

        stages = await call_tool(s, "showStages", {"project_id": 1})
        assert [st["id"] for st in stages["stages"]][:2] == ["plan", "script"]
        assert stages["stages"][0]["state"] == "ready"
        assert any(n["type"] == "plan" for n in stages["stages"][0]["nodes"])


async def test_edit_graph_proposes_and_apply_needs_confirm(db):
    async with db() as s:
        card = await call_tool(
            s,
            "editGraph",
            {
                "project_id": 1,
                "ops": [{"op": "set_node", "id": "n_music", "disabled": True}],
                "reason": "без музыки",
            },
        )
        assert card["needs_confirmation"] is True
        assert card["diff"]["summary"] == "~1 узл."
        assert card["will_reset"] == []
        pid = card["proposal_id"]

        again = await call_tool(s, "applyGraph", {"project_id": 1, "proposal_id": pid})
        assert again["needs_confirmation"] is True  # без confirm — только повтор карточки

        p = await s.get(Project, 1)
        assert "canvas_graph" not in (p.meta or {})

        done = await call_tool(s, "applyGraph", {"project_id": 1, "proposal_id": pid, "confirm": True})
        assert done["applied"] is True
        await s.refresh(p)
        assert p.meta["disabled_nodes"] == ["n_music"]

        with pytest.raises(ToolError):
            await call_tool(s, "applyGraph", {"project_id": 1, "proposal_id": pid, "confirm": True})


async def test_propose_whole_graph_without_positions_and_bad_graph_is_refused(db):
    async with db() as s:
        nodes = [
            {"id": "t", "type": "topic"},
            {"id": "p", "type": "plan"},
            {"id": "s", "type": "script"},
        ]
        edges = [{"source": "t", "target": "p"}, {"source": "p", "target": "s"}]
        card = await call_tool(
            s, "proposeGraph", {"project_id": 1, "nodes": nodes, "edges": edges, "reason": "коротко"}
        )
        assert card["needs_confirmation"] and card["diff"]["removed_nodes"]

        with pytest.raises(ToolError) as exc:
            await call_tool(
                s,
                "proposeGraph",
                {"project_id": 1, "nodes": nodes, "edges": edges + [{"source": "s", "target": "p"}]},
            )
        assert "цикл" in str(exc.value)

        with pytest.raises(ToolError):
            await call_tool(s, "editGraph", {"project_id": 1, "ops": [{"op": "remove_node", "id": "ghost"}]})


async def test_reset_step_asks_first_and_names_the_cone(db):
    async with db() as s:
        card = await call_tool(s, "resetStep", {"project_id": 1, "step_code": "img"})
        assert card["needs_confirmation"] is True
        assert "video" in card["will_reset"] and "plan" not in card["will_reset"]
        with pytest.raises(ToolError):
            await call_tool(s, "resetStep", {"project_id": 1, "step_code": "warp", "confirm": True})


async def test_set_project_options_validates(db):
    async with db() as s:
        with pytest.raises(ToolError) as exc:
            await call_tool(s, "setProjectOptions", {"project_id": 1, "options": {"video_resolution": "8k"}})
        assert "есть:" in str(exc.value)
        with pytest.raises(ToolError):
            await call_tool(s, "setProjectOptions", {"project_id": 1, "options": {"title": "x"}})
        out = await call_tool(s, "setProjectOptions", {"project_id": 1, "options": {"enrich_slots_count": 2}})
        assert out["set"] == {"enrich_slots_count": 2}
        p = await s.get(Project, 1)
        await s.refresh(p)
        assert p.enrich_slots_count == 2


async def test_run_stage_refuses_skipped_stage(db):
    async with db() as s:
        await call_tool(
            s,
            "editGraph",
            {"project_id": 1, "ops": [{"op": "set_node", "id": "n_plan", "disabled": True}]},
        )
        p = await s.get(Project, 1)
        pid = p.meta["graph_proposal"]["id"]
        await call_tool(s, "applyGraph", {"project_id": 1, "proposal_id": pid, "confirm": True})
        with pytest.raises(ToolError) as exc:
            await call_tool(s, "runStage", {"project_id": 1, "stage_id": "plan"})
        assert "выключена" in str(exc.value)


async def test_loop_carries_the_open_project_into_the_prompt(db):
    seen: list[str] = []

    async def _ask(prompt, system, history):
        seen.append(prompt)
        return json.dumps({"say": "ок"})

    async with db() as s:
        turn = await collect_turn(s, "что дальше?", ask=_ask, project_id=1)
    assert turn.reply == "ок"
    assert "project_id для инструментов — 1" in seen[0]


async def test_loop_shows_graph_then_answers(db):
    async with db() as s:
        turn = await collect_turn(
            s,
            "покажи схему",
            ask=_scripted(
                json.dumps({"tool": "showGraph", "args": {"project_id": 1}}), json.dumps({"say": "вот"})
            ),
        )
    kinds = [e.type for e in turn.events]
    assert kinds == ["tool_call", "tool_result", "message"]


# ── ветки отказов и успешные пути остальных инструментов ─────────────────


async def test_graph_tools_refuse_bad_arguments(db):
    async with db() as s:
        with pytest.raises(ToolError):
            await call_tool(s, "showGraph", {"project_id": 999})
        with pytest.raises(ToolError):
            await call_tool(s, "editGraph", {"project_id": 1, "ops": []})
        with pytest.raises(ToolError):
            await call_tool(s, "proposeGraph", {"project_id": 1, "nodes": [], "edges": []})
        with pytest.raises(ToolError):
            await call_tool(
                s, "proposeGraph", {"project_id": 1, "nodes": [{"id": "t", "type": "topic"}], "edges": "x"}
            )
        with pytest.raises(ToolError):
            await call_tool(s, "applyGraph", {"project_id": 1, "proposal_id": "none"})
        with pytest.raises(ToolError):
            await call_tool(s, "runStage", {"project_id": 1, "stage_id": "warp"})
        with pytest.raises(ToolError) as exc:
            await call_tool(s, "runStage", {"project_id": 1, "stage_id": "videos"})
        assert "недоступна" in str(exc.value)
        with pytest.raises(ToolError):
            await call_tool(s, "setProjectOptions", {"project_id": 1, "options": []})
        with pytest.raises(ToolError):
            await call_tool(
                s, "setProjectOptions", {"project_id": 1, "options": {"enrich_slots_count": "many"}}
            )
        with pytest.raises(ToolError):
            await call_tool(s, "setProjectOptions", {"project_id": 1, "options": {"enrich_slots_count": 9}})


async def test_show_graph_mentions_pending_proposal_and_discard_clears_it(db):
    async with db() as s:
        await call_tool(
            s, "editGraph", {"project_id": 1, "ops": [{"op": "set_node", "id": "n_music", "disabled": True}]}
        )
        shown = await call_tool(s, "showGraph", {"project_id": 1})
        assert shown["pending_proposal"]["diff"] == "~1 узл."
        out = await call_tool(s, "discardGraph", {"project_id": 1})
        assert out["discarded"] is True
        assert "pending_proposal" not in await call_tool(s, "showGraph", {"project_id": 1})


async def test_run_stage_asks_when_expensive_then_runs_stops_and_resets(db, monkeypatch):
    async with db() as s:
        monkeypatch.setattr("app.services.studio_agent.tools.CONFIRM_THRESHOLD_MICRO", -1)
        card = await call_tool(s, "runStage", {"project_id": 1, "stage_id": "plan"})
        assert card["needs_confirmation"] is True and card["stage_id"] == "plan"

        started = await call_tool(s, "runStage", {"project_id": 1, "stage_id": "plan", "confirm": True})
        assert started["started"] is True and started["step_code"] == "plan"
        p = await s.get(Project, 1)
        await s.refresh(p)
        assert p.status is ProjectStatus.planning

        stopped = await call_tool(s, "stopStep", {"project_id": 1})
        assert stopped["stopped"] is True

        reset = await call_tool(s, "resetStep", {"project_id": 1, "step_code": "plan", "confirm": True})
        assert reset["reset"] is True and reset["status"]


async def test_set_project_options_accepts_choices_and_flags(db):
    from app.generation_options import IMAGE_GENERATORS_BY_ID

    gen = next(iter(IMAGE_GENERATORS_BY_ID))
    async with db() as s:
        out = await call_tool(
            s, "setProjectOptions", {"project_id": 1, "options": {"image_generator": gen, "auto_mode": False}}
        )
        assert out["set"] == {"image_generator": gen, "auto_mode": False}


async def test_tool_errors_from_routers_become_tool_errors(db, monkeypatch):
    """Пятисотка роутера не должна долетать до чата: модель получает текст отказа."""
    from fastapi import HTTPException

    async def refuse(*_a, **_k):
        raise HTTPException(status_code=400, detail="занято")

    monkeypatch.setattr("app.web.routers.stages.run_stage", refuse)
    monkeypatch.setattr("app.web.routers.project_ops.reset_project_step", refuse)
    monkeypatch.setattr("app.web.routers.projects.patch_project", refuse)
    async with db() as s:
        for name, args in (
            ("runStage", {"project_id": 1, "stage_id": "plan", "confirm": True}),
            ("resetStep", {"project_id": 1, "step_code": "plan", "confirm": True}),
            ("setProjectOptions", {"project_id": 1, "options": {"auto_mode": True}}),
        ):
            with pytest.raises(ToolError) as exc:
                await call_tool(s, name, args)
            assert "занято" in str(exc.value)
