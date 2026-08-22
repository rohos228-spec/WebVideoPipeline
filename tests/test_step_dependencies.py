"""Тесты B.2/B.3: data-DAG шагов, проекция на порядок статусов, ord без коллизий."""

from __future__ import annotations

from types import SimpleNamespace

from app.models import ProjectStatus
from app.orchestrator.node_registry import WORK_NODES
from app.orchestrator.step_dependencies import (
    STEP_DEPENDENCIES,
    STEP_DEPENDENTS,
    TOPO_ORDER,
    canonical_codes,
    dependents_cone,
    is_step_enabled,
    known_step_codes,
    project_cone,
)
from app.telegram.menu import _STATUS_ORDER, status_order

# ── структура DAG ─────────────────────────────────────────────────────────


def test_dag_acyclic_and_closed():
    # Топо-порядок покрывает все узлы (циклы уронили бы обход рекурсией),
    # все родители — известные узлы.
    assert set(TOPO_ORDER) == set(STEP_DEPENDENCIES)
    for child, parents in STEP_DEPENDENCIES.items():
        for p in parents:
            assert p in STEP_DEPENDENCIES, f"{child}: неизвестный родитель {p}"
            assert TOPO_ORDER.index(p) < TOPO_ORDER.index(child)


def test_dependents_is_exact_inverse():
    edges = {(p, c) for c, ps in STEP_DEPENDENCIES.items() for p in ps}
    inv = {(p, c) for p, cs in STEP_DEPENDENTS.items() for c in cs}
    assert edges == inv


def test_every_work_node_step_is_in_dag():
    # Каждый шаг реестра нод известен графу (напрямую или алиасом).
    for spec in WORK_NODES.values():
        assert canonical_codes(spec.step_code), f"нет в DAG: {spec.step_code}"


# ── проекция на порядок статусов (B.2: проекция, не равенство) ────────────


def _step_status(code: str) -> tuple[ProjectStatus, ProjectStatus]:
    """step_code → (running, ready); для scene_d канон — веер sd_agent."""
    if code == "scene_d":
        spec = WORK_NODES["sd_agent"]
    else:
        spec = next(s for s in WORK_NODES.values() if s.step_code == code)
    return spec.running_status, spec.ready_status


def test_data_edges_respect_status_order():
    # Каждая data-зависимость достижима в порядке статусов:
    # родитель готов СТРОГО раньше, чем ребёнок начинает выполняться.
    for child, parents in STEP_DEPENDENCIES.items():
        child_running, _ = _step_status(child)
        for p in parents:
            _, parent_ready = _step_status(p)
            assert status_order(parent_ready) < status_order(child_running), (
                f"{p}({parent_ready}) не раньше {child}({child_running})"
            )


# ── конусы ────────────────────────────────────────────────────────────────


def test_cone_img_pr_matches_spec_scenario():
    # Сценарий спеки: смена промпта img_pr → images, anim_pr, videos
    # и финал; закадр/split/scene_design/hero — вне конуса.
    cone = set(dependents_cone("img_pr"))
    assert cone == {"img_pr", "img", "anim_pr", "video", "assemble", "publish"}


def test_cone_respects_music_independence():
    # Скip-set reset_step структурно: музыка — не потомок озвучки/видео.
    assert "music" not in dependents_cone("audio")
    assert "music" not in dependents_cone("video")
    assert "music" in dependents_cone("script")


def test_cone_scene_asm_does_not_touch_agents_upstream():
    # sd_* upstream сборщика: сброс scene_asm не сносит scene_d.
    assert "scene_d" not in dependents_cone("scene_asm")


def test_cone_split_reaches_everything_downstream():
    cone = set(dependents_cone("split"))
    assert {"img_pr", "img", "video", "audio", "assemble", "publish"} <= cone
    assert "plan" not in cone and "script" not in cone


def test_aliases_resolve():
    assert dependents_cone("sd_char") == dependents_cone("scene_d")
    assert set(canonical_codes("objects")) == {"hero", "items"}
    assert dependents_cone("no-such-step") == ()
    assert "sd_char" in known_step_codes()


# ── runtime-фильтр включённых шагов ───────────────────────────────────────


def _project(meta=None, slots=3):
    return SimpleNamespace(meta=meta or {}, enrich_slots_count=slots)


def test_enrich_slots_filtered_but_reachability_kept():
    p = _project(slots=3)
    cone = project_cone(p, "enrich_1")
    assert "enrich_4" not in cone and "enrich_5" not in cone
    # Достижимость через выключенные слоты сохраняется: img_pr в конусе.
    assert "img_pr" in cone and "img" in cone


def test_scene_design_filtered_by_project_flag():
    off = _project(meta={"scene_design_enabled": False})
    on = _project(meta={"scene_design_enabled": True})
    assert not is_step_enabled(off, "scene_d")
    assert is_step_enabled(on, "scene_asm")
    assert "scene_asm" not in project_cone(off, "split")
    assert "scene_asm" in project_cone(on, "split")


# ── ord без коллизий (B.3, §9#3) ──────────────────────────────────────────


def test_status_order_no_collisions():
    # Терминальные paused/failed сознательно делят 0 с new — исключаем.
    terminal = {ProjectStatus.new, ProjectStatus.paused, ProjectStatus.failed}
    seen: dict[int, ProjectStatus] = {}
    for status, ord_ in _STATUS_ORDER.items():
        if status in terminal:
            continue
        assert ord_ not in seen, f"коллизия ord {ord_}: {seen[ord_]} и {status}"
        seen[ord_] = status
