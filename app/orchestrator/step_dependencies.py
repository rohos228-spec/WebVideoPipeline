"""Явный граф data-зависимостей шагов (этап 2, cache-resume).

Единственный источник ответа «что пересчитывать при смене входа шага X».
Это чистый data-DAG «выход X — вход Y»:

- НЕ порядок статусов (`_STATUS_ORDER` в menu.py — с коллизиями ord)
  и НЕ `StepDef.requires` (пререквизиты ЗАПУСКА: hero requires
  frames_ready, но данные hero от split не зависят иначе как через xlsx);
- БЕЗ back-edges: возвраты vision_check_loop — механика проверки,
  не зависимость данных;
- опциональные шаги (scene_*, sfx_*, enrich-слоты) присутствуют
  статически; конус конкретного проекта фильтруется `project_cone` —
  рёбра ЧЕРЕЗ выключенный шаг сохраняют достижимость (шаг просто
  не сбрасывается сам).

Скip-set'ы reset_step (`_RESET_SKIP_DOWNSTREAM`: музыка независима от
озвучки, sd_* upstream сборщика) здесь выражены структурно: music —
не потомок audio/video, scene_d — не потомок scene_asm.
"""

from __future__ import annotations

from typing import Any

# step_code → data-родители (чей выход — вход этого шага).
#
# Обоснование неочевидных рёбер (карта system-map §2):
# - hero ← split: hero читает лист «Персонажи» project.xlsx (пересоздаётся
#   split'ом); scene_asm hero НЕ читает (scene_registry — не его вход).
# - enrich_1 ← split+hero+items: правит project.xlsx поверх кадров и
#   референсов; слоты — цепочкой (каждый видит правки предыдущего).
# - img_pr ← enrich_5 + scene_asm: backfill по кадрам после правок +
#   scene_registry/attrs кадров от сборщика сцен.
# - img ← img_pr + hero + items: PNG генерится из image_prompt И
#   референсов героя/предметов — регенерация hero инвалидирует картинки.
# - audio ← enrich_5: voiceover_text кадров правится apply-ops enrich'а.
# - music ← script: voiceover.txt + topic; от audio/video НЕ зависит.
# - assemble ← video+audio+music+sfx_gen: клипы, озвучка, музыка, звуки.
STEP_DEPENDENCIES: dict[str, tuple[str, ...]] = {
    "plan": (),
    "script": ("plan",),
    "split": ("script",),
    "scene_d": ("split",),
    "scene_asm": ("scene_d",),
    "hero": ("split",),
    "items": ("hero",),
    "enrich_1": ("split", "hero", "items"),
    "enrich_2": ("enrich_1",),
    "enrich_3": ("enrich_2",),
    "enrich_4": ("enrich_3",),
    "enrich_5": ("enrich_4",),
    "img_pr": ("enrich_5", "scene_asm"),
    "img": ("img_pr", "hero", "items"),
    "anim_pr": ("img",),
    "video": ("anim_pr", "img"),
    "audio": ("enrich_5",),
    "music": ("script",),
    "sfx_plan": ("split",),
    "sfx_gen": ("sfx_plan",),
    "assemble": ("video", "audio", "music", "sfx_gen"),
    "publish": ("assemble",),
}

# Алиасы «как шаг зовут снаружи» → канонические коды DAG.
# sd_* — агенты веера scene_design (per-agent перезапуск); wrapper'ы
# objects/enrich — из reset_step._WRAPPER_TO_CODES.
STEP_ALIASES: dict[str, tuple[str, ...]] = {
    "sd_skel": ("scene_d",),
    "sd_char": ("scene_d",),
    "sd_world": ("scene_d",),
    "sd_style": ("scene_d",),
    "sd_cam": ("scene_d",),
    "sd_act": ("scene_d",),
    "objects": ("hero", "items"),
    "enrich": ("enrich_1", "enrich_2", "enrich_3", "enrich_4", "enrich_5"),
}


def canonical_codes(step_code: str) -> tuple[str, ...]:
    """Внешний код шага → канонические узлы DAG (пусто, если неизвестен)."""
    if step_code in STEP_DEPENDENCIES:
        return (step_code,)
    return STEP_ALIASES.get(step_code, ())


def _invert() -> dict[str, tuple[str, ...]]:
    children: dict[str, list[str]] = {c: [] for c in STEP_DEPENDENCIES}
    for child, parents in STEP_DEPENDENCIES.items():
        for p in parents:
            children[p].append(child)
    return {k: tuple(v) for k, v in children.items()}


STEP_DEPENDENTS: dict[str, tuple[str, ...]] = _invert()


def _topo_order() -> tuple[str, ...]:
    order: list[str] = []
    seen: set[str] = set()

    def visit(code: str) -> None:
        if code in seen:
            return
        seen.add(code)
        for p in STEP_DEPENDENCIES[code]:
            visit(p)
        order.append(code)

    for c in STEP_DEPENDENCIES:
        visit(c)
    return tuple(order)


TOPO_ORDER: tuple[str, ...] = _topo_order()


def dependents_cone(step_code: str, *, include_self: bool = True) -> tuple[str, ...]:
    """Конус вниз: шаги, чей вход (транзитивно) зависит от выхода step_code.

    Результат — в топологическом порядке (родители раньше детей).
    Неизвестный код → пустой кортеж.
    """
    roots = canonical_codes(step_code)
    if not roots:
        return ()
    cone: set[str] = set()
    stack = list(roots)
    while stack:
        cur = stack.pop()
        for child in STEP_DEPENDENTS[cur]:
            if child not in cone:
                cone.add(child)
                stack.append(child)
    if include_self:
        cone.update(roots)
    return tuple(c for c in TOPO_ORDER if c in cone)


def is_step_enabled(project: Any, step_code: str) -> bool:
    """Включён ли шаг у проекта (runtime-фильтр конуса).

    Рёбра через выключенный шаг сохраняют достижимость — фильтр решает
    только «сбрасывать ли сам шаг», не рвёт пути.
    """
    if step_code in ("scene_d", "scene_asm"):
        from app.services.scene_design.runner import scene_design_enabled

        return scene_design_enabled(project)
    if step_code in ("sfx_plan", "sfx_gen"):
        from app.settings import settings

        return bool(settings.sfx_enabled)
    if step_code.startswith("enrich_"):
        from app.orchestrator.pipeline_steps import enabled_enrich_slots

        try:
            slot = int(step_code.rsplit("_", 1)[1])
        except ValueError:
            return True
        return slot <= enabled_enrich_slots(project)
    return True


def project_cone(project: Any, step_code: str, *, include_self: bool = True) -> tuple[str, ...]:
    """Конус инвалидации для конкретного проекта: DAG × включённые шаги."""
    return tuple(
        c for c in dependents_cone(step_code, include_self=include_self) if is_step_enabled(project, c)
    )


def known_step_codes() -> frozenset[str]:
    return frozenset(STEP_DEPENDENCIES) | frozenset(STEP_ALIASES)
