"""Единый реестр шагов и метаданных конвейера (Single Source of Truth).

Шаги конвейера:
  1: plan      → planning            → plan_ready
  2: script    → scripting           → script_ready
  3: split     → splitting           → frames_ready
  4: objects   → (sub-steps: hero, items)
       4a: hero  → generating_hero   → hero_ready
       4b: items → generating_items  → items_ready
  5: enrich   → (sub-steps: enrich_1..5)
  6: img_pr   → generating_image_prompts → image_prompts_ready
  7: img      → generating_images   → images_ready
  8: anim_pr  → generating_animation_prompts → animation_prompts_ready
  9: video    → generating_videos   → videos_ready
 10: music    → generating_music    → music_ready
 11: assemble → assembling          → assembled

Sub-steps:
  scene_d     → scene_designing     → scene_agents_ready
  scene_asm   → scene_assembling    → scene_design_ready
  audio       → generating_audio    → audio_ready
  sfx_plan    → sfx_planning        → sfx_plan_ready
  sfx_gen     → generating_sfx      → sfx_ready
"""

from __future__ import annotations

from dataclasses import dataclass

from app.models import Project, ProjectStatus


@dataclass(frozen=True)
class StepDef:
    n: int
    code: str
    title: str
    running_status: ProjectStatus
    ready_status: ProjectStatus
    requires: ProjectStatus | None


# Порядок «достижимости» статусов — для проверки prerequisite.
# Чем выше число, тем «дальше» по пайплайну проект.
_STATUS_ORDER: dict[ProjectStatus, int] = {
    ProjectStatus.new: 0,
    ProjectStatus.planning: 1,
    ProjectStatus.plan_ready: 2,
    ProjectStatus.scripting: 3,
    ProjectStatus.script_ready: 4,
    ProjectStatus.splitting: 5,
    ProjectStatus.frames_ready: 6,
    ProjectStatus.scene_designing: 7,
    ProjectStatus.scene_agents_ready: 8,
    ProjectStatus.scene_assembling: 9,
    ProjectStatus.scene_design_ready: 10,
    ProjectStatus.generating_hero: 11,
    ProjectStatus.hero_ready: 12,
    ProjectStatus.generating_items: 13,
    ProjectStatus.items_ready: 14,
    ProjectStatus.enriching_1: 15,
    ProjectStatus.enrich_1_ready: 16,
    ProjectStatus.enriching_2: 17,
    ProjectStatus.enrich_2_ready: 18,
    ProjectStatus.enriching_3: 19,
    ProjectStatus.enrich_3_ready: 20,
    ProjectStatus.enriching_4: 21,
    ProjectStatus.enrich_4_ready: 22,
    ProjectStatus.enriching_5: 23,
    ProjectStatus.enrich_5_ready: 24,
    ProjectStatus.generating_image_prompts: 25,
    ProjectStatus.image_prompts_ready: 26,
    ProjectStatus.generating_images: 27,
    ProjectStatus.images_ready: 28,
    ProjectStatus.generating_animation_prompts: 29,
    ProjectStatus.animation_prompts_ready: 30,
    ProjectStatus.generating_videos: 31,
    ProjectStatus.videos_ready: 32,
    ProjectStatus.generating_audio: 33,
    ProjectStatus.audio_ready: 34,
    ProjectStatus.generating_music: 35,
    ProjectStatus.music_ready: 36,
    ProjectStatus.sfx_planning: 37,
    ProjectStatus.sfx_plan_ready: 38,
    ProjectStatus.generating_sfx: 39,
    ProjectStatus.sfx_ready: 40,
    ProjectStatus.assembling: 41,
    ProjectStatus.assembled: 42,
    ProjectStatus.publishing: 43,
    ProjectStatus.published: 44,
    ProjectStatus.paused: 0,
    ProjectStatus.failed: 0,
}


ENRICH_RUNNING: list[ProjectStatus] = [
    ProjectStatus.enriching_1,
    ProjectStatus.enriching_2,
    ProjectStatus.enriching_3,
    ProjectStatus.enriching_4,
    ProjectStatus.enriching_5,
]
ENRICH_READY: list[ProjectStatus] = [
    ProjectStatus.enrich_1_ready,
    ProjectStatus.enrich_2_ready,
    ProjectStatus.enrich_3_ready,
    ProjectStatus.enrich_4_ready,
    ProjectStatus.enrich_5_ready,
]
MAX_ENRICH_SLOTS = 5


def _objects_requires_for_step5() -> ProjectStatus:
    return ProjectStatus.hero_ready


def enabled_enrich_slots(project: Project | None) -> int:
    """Сколько enrich-слотов реально включено у проекта (1..5)."""
    if project is None:
        return 3
    graph_slots = _enrich_slots_in_canvas(project)
    if graph_slots is not None:
        return max(1, min(MAX_ENRICH_SLOTS, graph_slots))
    n = project.enrich_slots_count or 3
    return max(1, min(MAX_ENRICH_SLOTS, n))


def _enrich_slots_in_canvas(project: Project) -> int | None:
    try:
        from app.services.canvas_graph import canvas_graph_from_meta
        from app.services.excel_gpt_node import (
            is_excel_gpt_node_type,
            sd_agent_marker,
            slot_index_from_node,
        )

        meta = project.meta if isinstance(project.meta, dict) else {}
        cg = canvas_graph_from_meta(meta)
        if not cg:
            return None
        slots: set[int] = set()
        for node in cg.get("nodes") or []:
            if not isinstance(node, dict):
                continue
            if not is_excel_gpt_node_type(str(node.get("type") or "")):
                continue
            if sd_agent_marker(node):
                continue
            slot = slot_index_from_node(node)
            if slot >= 1:
                slots.add(slot)
        return len(slots) or None
    except Exception:  # noqa: BLE001
        return None


def steps_for(project: Project | None) -> list[StepDef]:
    """Динамический список базовых шагов конвейера для проекта."""
    n_slots = enabled_enrich_slots(project)
    enrich_ready = ENRICH_READY[n_slots - 1]
    return [
        StepDef(
            1,
            "plan",
            "Сценарий",
            ProjectStatus.planning,
            ProjectStatus.plan_ready,
            None,
        ),
        StepDef(
            2,
            "script",
            "Закадровый текст",
            ProjectStatus.scripting,
            ProjectStatus.script_ready,
            ProjectStatus.plan_ready,
        ),
        StepDef(
            3,
            "split",
            "Разбивка на блоки",
            ProjectStatus.splitting,
            ProjectStatus.frames_ready,
            ProjectStatus.script_ready,
        ),
        StepDef(
            4,
            "objects",
            "Объекты",
            ProjectStatus.generating_hero,
            ProjectStatus.hero_ready,
            ProjectStatus.frames_ready,
        ),
        StepDef(
            5,
            "enrich",
            "Доработка данных",
            ProjectStatus.enriching_1,
            enrich_ready,
            _objects_requires_for_step5(),
        ),
        StepDef(
            6,
            "img_pr",
            "Промты картинок",
            ProjectStatus.generating_image_prompts,
            ProjectStatus.image_prompts_ready,
            enrich_ready,
        ),
        StepDef(
            7,
            "img",
            "Картинки",
            ProjectStatus.generating_images,
            ProjectStatus.images_ready,
            ProjectStatus.image_prompts_ready,
        ),
        StepDef(
            8,
            "anim_pr",
            "Промты анимации",
            ProjectStatus.generating_animation_prompts,
            ProjectStatus.animation_prompts_ready,
            ProjectStatus.images_ready,
        ),
        StepDef(
            9,
            "video",
            "Видео",
            ProjectStatus.generating_videos,
            ProjectStatus.videos_ready,
            ProjectStatus.animation_prompts_ready,
        ),
        StepDef(
            10,
            "music",
            "Музыка",
            ProjectStatus.generating_music,
            ProjectStatus.music_ready,
            ProjectStatus.audio_ready,
        ),
        StepDef(
            11,
            "assemble",
            "Финальная сборка",
            ProjectStatus.assembling,
            ProjectStatus.assembled,
            ProjectStatus.audio_ready,
        ),
    ]


def _enrich_slot_step(slot: int) -> StepDef:
    return StepDef(
        -1,
        f"enrich_{slot}",
        f"Доработка данных #{slot}",
        ENRICH_RUNNING[slot - 1],
        ENRICH_READY[slot - 1],
        ENRICH_READY[slot - 2] if slot > 1 else _objects_requires_for_step5(),
    )


STEPS: list[StepDef] = steps_for(None)

_STEP_BY_CODE: dict[str, StepDef] = {s.code: s for s in STEPS}
_STEP_BY_CODE["hero"] = StepDef(
    -1,
    "hero",
    "Персонажи",
    ProjectStatus.generating_hero,
    ProjectStatus.hero_ready,
    ProjectStatus.frames_ready,
)
_STEP_BY_CODE["scene_d"] = StepDef(
    -1,
    "scene_d",
    "Сцены: агенты",
    ProjectStatus.scene_designing,
    ProjectStatus.scene_agents_ready,
    ProjectStatus.frames_ready,
)
_STEP_BY_CODE["scene_asm"] = StepDef(
    -1,
    "scene_asm",
    "Сцены: сборка",
    ProjectStatus.scene_assembling,
    ProjectStatus.scene_design_ready,
    ProjectStatus.scene_agents_ready,
)
for _code, _title in (
    ("sd_skel", "Агент: скелет"),
    ("sd_char", "Агент: персонажи"),
    ("sd_world", "Агент: мир"),
    ("sd_style", "Агент: стиль"),
    ("sd_cam", "Агент: камера"),
    ("sd_act", "Агент: действие"),
):
    _STEP_BY_CODE[_code] = StepDef(
        -1,
        _code,
        _title,
        ProjectStatus.scene_designing,
        ProjectStatus.scene_agents_ready,
        ProjectStatus.frames_ready,
    )
_STEP_BY_CODE["audio"] = StepDef(
    -1,
    "audio",
    "Озвучка",
    ProjectStatus.generating_audio,
    ProjectStatus.audio_ready,
    ProjectStatus.videos_ready,
)
_STEP_BY_CODE["sfx_plan"] = StepDef(
    -1,
    "sfx_plan",
    "План звуков",
    ProjectStatus.sfx_planning,
    ProjectStatus.sfx_plan_ready,
    ProjectStatus.music_ready,
)
_STEP_BY_CODE["sfx_gen"] = StepDef(
    -1,
    "sfx_gen",
    "Звуки (SFX)",
    ProjectStatus.generating_sfx,
    ProjectStatus.sfx_ready,
    ProjectStatus.sfx_plan_ready,
)
_STEP_BY_CODE["items"] = StepDef(
    -1,
    "items",
    "Предметы",
    ProjectStatus.generating_items,
    ProjectStatus.items_ready,
    ProjectStatus.hero_ready,
)
for _slot in range(1, MAX_ENRICH_SLOTS + 1):
    _code = f"enrich_{_slot}"
    _STEP_BY_CODE[_code] = _enrich_slot_step(_slot)


def step_by_code(code: str) -> StepDef | None:
    return _STEP_BY_CODE.get(code)


def step_by_running_status(running_status: ProjectStatus) -> StepDef | None:
    for code in (
        "hero",
        "items",
        "audio",
        "scene_d",
        "scene_asm",
        *(f"enrich_{i}" for i in range(1, MAX_ENRICH_SLOTS + 1)),
    ):
        sd = _STEP_BY_CODE.get(code)
        if sd is not None and sd.running_status is running_status:
            return sd
    for sd in STEPS:
        if sd.code in ("objects", "enrich"):
            continue
        if sd.running_status is running_status:
            return sd
    return None


def status_order(s: ProjectStatus) -> int:
    return _STATUS_ORDER.get(s, 0)


def step_icon(step: StepDef, project_status: ProjectStatus) -> str:
    if step.code == "enrich":
        if project_status in ENRICH_RUNNING:
            return "⏳"
        if status_order(project_status) >= status_order(step.ready_status):
            return "✅"
        return "⬜"
    if project_status is step.running_status:
        return "⏳"
    if status_order(project_status) >= status_order(step.ready_status):
        return "✅"
    return "⬜"
