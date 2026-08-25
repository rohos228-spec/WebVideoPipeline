"""Семь стадий пайплайна — то, что видит пользователь.

Внутри пайплайна два десятка статусов: сцен-дизайн разложен на веер агентов
и сборщик, «объекты» — на персонажей и предметы, доработка таблицы — на пять
слотов, финал — на озвучку, музыку, звуки и сборку. Пользователю этот развал
не нужен: он пишет идею, правит текст и платит за результат. Поэтому здесь
цепочка статусов сворачивается в семь стадий, у каждой — одна цена, одна
кнопка и один финишный статус.

Стадия запускается как «гнать цепочку до финишного статуса»: в meta проекта
кладётся ``stage_run`` с целью, включается auto_mode, и авто-продвижение
доводит проект до цели, после чего гейт (``stage_gate_reached``) выключает
auto_mode. Так пользователь получает шаг целиком, а не по одному статусу, и
перезагрузка страницы ничего не ломает — цель живёт на сервере.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.models import Project, ProjectStatus

# ── Линейный порядок статусов ────────────────────────────────────────────
# Ранг статуса отвечает на вопрос «стадия уже позади?». Порядок повторяет
# карту переходов в app/orchestrator/auto_advance.py — единственное место,
# где цепочка задана по-настоящему.
STATUS_ORDER: tuple[ProjectStatus, ...] = (
    ProjectStatus.new,
    ProjectStatus.planning,
    ProjectStatus.plan_ready,
    ProjectStatus.scripting,
    ProjectStatus.script_ready,
    ProjectStatus.splitting,
    ProjectStatus.frames_ready,
    ProjectStatus.scene_designing,
    ProjectStatus.scene_agents_ready,
    ProjectStatus.scene_assembling,
    ProjectStatus.scene_design_ready,
    ProjectStatus.generating_hero,
    ProjectStatus.hero_ready,
    ProjectStatus.generating_items,
    ProjectStatus.items_ready,
    ProjectStatus.enriching_1,
    ProjectStatus.enrich_1_ready,
    ProjectStatus.enriching_2,
    ProjectStatus.enrich_2_ready,
    ProjectStatus.enriching_3,
    ProjectStatus.enrich_3_ready,
    ProjectStatus.enriching_4,
    ProjectStatus.enrich_4_ready,
    ProjectStatus.enriching_5,
    ProjectStatus.enrich_5_ready,
    ProjectStatus.generating_image_prompts,
    ProjectStatus.image_prompts_ready,
    ProjectStatus.generating_images,
    ProjectStatus.images_ready,
    ProjectStatus.generating_animation_prompts,
    ProjectStatus.animation_prompts_ready,
    ProjectStatus.generating_videos,
    ProjectStatus.videos_ready,
    ProjectStatus.generating_audio,
    ProjectStatus.audio_ready,
    ProjectStatus.generating_music,
    ProjectStatus.music_ready,
    ProjectStatus.sfx_planning,
    ProjectStatus.sfx_plan_ready,
    ProjectStatus.generating_sfx,
    ProjectStatus.sfx_ready,
    ProjectStatus.assembling,
    ProjectStatus.assembled,
    ProjectStatus.publishing,
    ProjectStatus.published,
)

_RANK: dict[ProjectStatus, int] = {st: i for i, st in enumerate(STATUS_ORDER)}

# Статусы вне линии: paused/failed говорят не «где проект», а «что случилось».
OFF_LINE_STATUSES = frozenset({ProjectStatus.paused, ProjectStatus.failed})


def status_rank(status: ProjectStatus) -> int:
    """Позиция статуса в цепочке; -1 для paused/failed."""
    return _RANK.get(status, -1)


# ── Определения стадий ───────────────────────────────────────────────────


@dataclass(frozen=True)
class Stage:
    id: str
    label: str
    hint: str
    #: Финишный статус: досюда гоним цепочку.
    target: ProjectStatus
    #: Код шага для явного (пере)запуска стадии с начала.
    entry_step: str
    #: Ключи смет, из которых складывается цена стадии.
    price_keys: tuple[str, ...] = ()
    #: Что пользователь правит на этой стадии (подсказка фронту).
    editor: str = ""
    #: Динамические ключи смет — считаются от проекта (enrich-слоты).
    dynamic_price: str = ""
    #: Коды шагов, чьи промты относятся к этой стадии.
    #:
    #: Стадия сворачивает несколько статусов, и промтов у неё столько же.
    #: Список объявлен здесь, а не на фронте, потому что фронт не может знать
    #: состав стадии: он видит семь карточек, а за «Героями и предметами»
    #: стоят шесть разных папок промтов. Пустой кортеж — честное «править
    #: нечего» (у озвучки и сборки промтов нет вовсе).
    prompt_steps: tuple[str, ...] = ()


STAGES: tuple[Stage, ...] = (
    Stage(
        id="plan",
        label="Сценарий",
        hint="ИИ раскладывает идею на крючок, развитие и финал",
        target=ProjectStatus.plan_ready,
        entry_step="plan",
        price_keys=("plan",),
        editor="plan",
        prompt_steps=("plan",),
    ),
    Stage(
        id="script",
        label="Закадровый текст",
        hint="Сценарий превращается в текст, который прочитает голос",
        target=ProjectStatus.script_ready,
        entry_step="script",
        price_keys=("script",),
        editor="script",
        prompt_steps=("script",),
    ),
    Stage(
        id="frames",
        label="Разбивка на кадры",
        hint="Текст режется на кадры с длительностью",
        target=ProjectStatus.frames_ready,
        entry_step="split",
        price_keys=("split",),
        editor="frames",
        prompt_steps=("split",),
    ),
    Stage(
        id="cast",
        label="Герои и предметы",
        hint="Референсы персонажей и предметов, разбор сцен",
        target=ProjectStatus.enrich_5_ready,  # уточняется по слотам проекта
        entry_step="objects",
        price_keys=("scene_d", "scene_asm", "hero", "items"),
        editor="cast",
        dynamic_price="enrich",
        prompt_steps=("scene_d", "hero", "hero_style", "items", "excel_gpt"),
    ),
    Stage(
        id="images",
        label="Картинки",
        hint="Промты кадров и сами кадры",
        target=ProjectStatus.images_ready,
        entry_step="img_pr",
        price_keys=("img_pr", "img"),
        editor="images",
        prompt_steps=("img_pr",),
    ),
    Stage(
        id="videos",
        label="Видео",
        hint="Промты анимации и оживление кадров",
        target=ProjectStatus.videos_ready,
        entry_step="anim_pr",
        price_keys=("anim_pr", "video"),
        editor="videos",
        prompt_steps=("anim_pr",),
    ),
    Stage(
        id="final",
        label="Озвучка и сборка",
        hint="Голос, музыка, звуки и финальный монтаж",
        target=ProjectStatus.assembled,
        entry_step="audio",
        price_keys=("audio", "sfx_plan"),
        editor="final",
    ),
)

STAGE_BY_ID: dict[str, Stage] = {s.id: s for s in STAGES}

_ENRICH_READY: tuple[ProjectStatus, ...] = (
    ProjectStatus.enrich_1_ready,
    ProjectStatus.enrich_2_ready,
    ProjectStatus.enrich_3_ready,
    ProjectStatus.enrich_4_ready,
    ProjectStatus.enrich_5_ready,
)


def _enrich_slots(project: Project) -> int:
    raw = getattr(project, "enrich_slots_count", None)
    try:
        n = int(raw or 3)
    except (TypeError, ValueError):
        n = 3
    return max(1, min(5, n))


def stage_target(project: Project, stage: Stage) -> ProjectStatus:
    """Финишный статус стадии для конкретного проекта.

    У «героев и предметов» финиш зависит от числа enrich-слотов: проект с
    тремя слотами заканчивает стадию на enrich_3_ready, и ждать пятого —
    значит зависнуть навсегда.
    """
    if stage.id == "cast":
        return _ENRICH_READY[_enrich_slots(project) - 1]
    return stage.target


def stage_price_keys(project: Project, stage: Stage) -> list[str]:
    keys = list(stage.price_keys)
    if stage.dynamic_price == "enrich":
        keys.extend(f"enrich_{i}" for i in range(1, _enrich_slots(project) + 1))
    return keys


def entry_step_code(project: Project, stage: Stage) -> str:
    """Код шага, с которого стадия стартует заново.

    Сцен-дизайн включается флагом: когда он включён, «герои и предметы»
    начинаются с веера агентов, иначе — сразу с персонажей.
    """
    if stage.id == "cast":
        try:
            from app.services.scene_design import scene_design_enabled

            if scene_design_enabled(project):
                return "scene_d"
        except Exception:  # noqa: BLE001
            pass
        return "objects"
    return stage.entry_step


# ── Состояние стадий для UI ──────────────────────────────────────────────


@dataclass
class StageState:
    stage: Stage
    state: str  # locked | ready | running | done | failed | paused
    target: ProjectStatus
    price_keys: list[str] = field(default_factory=list)


def _stage_bounds(project: Project) -> list[tuple[Stage, int]]:
    """(стадия, ранг её финишного статуса) в порядке цепочки."""
    return [(s, status_rank(stage_target(project, s))) for s in STAGES]


def stage_states(project: Project) -> list[StageState]:
    """Разложить текущий статус проекта по семи стадиям."""
    from app.services.project_state import is_running_status

    status = project.status
    bounds = _stage_bounds(project)
    off_line = status in OFF_LINE_STATUSES
    rank = status_rank(status)
    if off_line:
        # paused/failed: ранг берём с последнего известного линейного статуса.
        meta = project.meta if isinstance(project.meta, dict) else {}
        last = meta.get("last_linear_status")
        try:
            rank = status_rank(ProjectStatus(last)) if last else -1
        except ValueError:
            rank = -1

    running = is_running_status(status)
    out: list[StageState] = []
    first_open = True
    for stage, end_rank in bounds:
        target = stage_target(project, stage)
        keys = stage_price_keys(project, stage)
        if rank >= end_rank >= 0 and rank >= 0:
            state = "done"
        elif running and rank >= 0 and rank < end_rank and first_open:
            state = "running"
            first_open = False
        elif first_open:
            state = "failed" if status is ProjectStatus.failed else ("paused" if off_line else "ready")
            first_open = False
        else:
            state = "locked"
        out.append(StageState(stage=stage, state=state, target=target, price_keys=keys))
    return out


# ── Гейт: «доехали до конца стадии» ──────────────────────────────────────


def stage_run_meta(project: Project) -> dict[str, Any] | None:
    meta = project.meta if isinstance(project.meta, dict) else {}
    run = meta.get("stage_run")
    return run if isinstance(run, dict) else None


def begin_stage_run(project: Project, stage: Stage) -> dict[str, Any]:
    """Записать цель стадии в meta и включить авто-продвижение до неё."""
    from sqlalchemy.orm.attributes import flag_modified

    target = stage_target(project, stage)
    meta = dict(project.meta or {}) if isinstance(project.meta, dict) else {}
    run = {"stage": stage.id, "target": target.value}
    meta["stage_run"] = run
    meta.pop("user_stop", None)
    project.meta = meta
    flag_modified(project, "meta")
    project.auto_mode = True
    return run


def clear_stage_run(project: Project) -> bool:
    """Снять цель стадии и выключить авто-продвижение."""
    from sqlalchemy.orm.attributes import flag_modified

    meta = dict(project.meta or {}) if isinstance(project.meta, dict) else {}
    had = meta.pop("stage_run", None) is not None
    if had:
        project.meta = meta
        flag_modified(project, "meta")
    if project.auto_mode:
        project.auto_mode = False
        had = True
    return had


def stage_gate_reached(project: Project) -> bool:
    """Проект доехал до цели активной стадии — дальше не гнать.

    Вызывается из авто-продвижения перед переходом. Без активной цели
    (``meta.stage_run`` пуст) гейт молчит: старые режимы работают как раньше.
    """
    run = stage_run_meta(project)
    if not run:
        return False
    raw = str(run.get("target") or "")
    try:
        target = ProjectStatus(raw)
    except ValueError:
        return True  # мусор в meta — считаем стадию завершённой, не гоняем цепь
    cur = status_rank(project.status)
    tgt = status_rank(target)
    if cur < 0 or tgt < 0:
        return False
    return cur >= tgt
