"""Семь стадий: раскладка статусов, цель стадии и гейт авто-продвижения."""

from __future__ import annotations

from app.models import Project, ProjectStatus
from app.services.pipeline_stages import (
    STAGES,
    STATUS_ORDER,
    begin_stage_run,
    clear_stage_run,
    entry_step_code,
    stage_gate_reached,
    stage_price_keys,
    stage_states,
    stage_target,
    status_rank,
)


def _project(status: ProjectStatus, **kw) -> Project:
    p = Project(slug="t", title="t", topic="t", status=status, meta={})
    for k, v in kw.items():
        setattr(p, k, v)
    return p


def _by_id(project: Project) -> dict[str, str]:
    return {st.stage.id: st.state for st in stage_states(project)}


def test_status_order_covers_every_transition_target():
    """Каждый статус из цепочки переходов имеет ранг — иначе стадия зависнет."""
    from app.orchestrator.auto_advance import TRANSITIONS

    for ready, transition in TRANSITIONS.items():
        assert status_rank(ready) >= 0, f"{ready} вне STATUS_ORDER"
        nxt = transition.next_running
        if nxt is not None:
            assert status_rank(nxt) >= 0, f"{nxt} вне STATUS_ORDER"


def test_status_order_has_no_duplicates():
    assert len(STATUS_ORDER) == len(set(STATUS_ORDER))


def test_new_project_opens_on_first_stage():
    states = _by_id(_project(ProjectStatus.new))
    assert states["plan"] == "ready"
    assert states["script"] == "locked"
    assert states["final"] == "locked"


def test_done_stages_are_behind_and_next_is_ready():
    states = _by_id(_project(ProjectStatus.frames_ready))
    assert states["plan"] == "done"
    assert states["script"] == "done"
    assert states["frames"] == "done"
    assert states["cast"] == "ready"
    assert states["images"] == "locked"


def test_running_status_marks_its_stage():
    states = _by_id(_project(ProjectStatus.generating_images))
    assert states["images"] == "running"
    assert states["frames"] == "done"
    assert states["videos"] == "locked"


def test_assembled_closes_every_stage():
    assert set(_by_id(_project(ProjectStatus.assembled)).values()) == {"done"}


def test_cast_target_follows_enrich_slots():
    """Проект с тремя слотами не должен ждать пятого — он не наступит."""
    cast = next(s for s in STAGES if s.id == "cast")
    assert stage_target(_project(ProjectStatus.new, enrich_slots_count=3), cast) is (
        ProjectStatus.enrich_3_ready
    )
    assert stage_target(_project(ProjectStatus.new, enrich_slots_count=5), cast) is (
        ProjectStatus.enrich_5_ready
    )
    keys = stage_price_keys(_project(ProjectStatus.new, enrich_slots_count=2), cast)
    assert "enrich_1" in keys and "enrich_2" in keys and "enrich_3" not in keys


def test_cast_entry_step_follows_scene_design_flag():
    cast = next(s for s in STAGES if s.id == "cast")
    assert (
        entry_step_code(_project(ProjectStatus.new, meta={"scene_design_enabled": True}), cast) == "scene_d"
    )
    assert (
        entry_step_code(_project(ProjectStatus.new, meta={"scene_design_enabled": False}), cast) == "objects"
    )


def test_gate_silent_without_active_stage():
    """Без stage_run гейт не вмешивается: старые режимы работают как раньше."""
    assert stage_gate_reached(_project(ProjectStatus.plan_ready)) is False


def test_gate_holds_until_target_and_then_stops():
    p = _project(ProjectStatus.new)
    plan = next(s for s in STAGES if s.id == "plan")
    begin_stage_run(p, plan)
    assert p.auto_mode is True
    assert p.meta["stage_run"] == {"stage": "plan", "target": "plan_ready"}

    p.status = ProjectStatus.planning
    assert stage_gate_reached(p) is False

    p.status = ProjectStatus.plan_ready
    assert stage_gate_reached(p) is True

    assert clear_stage_run(p) is True
    assert p.auto_mode is False
    assert "stage_run" not in p.meta
    assert stage_gate_reached(p) is False


def test_gate_stops_when_chain_overshoots_target():
    """Если цепочка проскочила цель, гнать дальше тем более нельзя."""
    p = _project(ProjectStatus.new)
    begin_stage_run(p, next(s for s in STAGES if s.id == "frames"))
    p.status = ProjectStatus.generating_hero
    assert stage_gate_reached(p) is True


def test_gate_stops_on_broken_target():
    p = _project(ProjectStatus.plan_ready, meta={"stage_run": {"stage": "plan", "target": "мусор"}})
    assert stage_gate_reached(p) is True
