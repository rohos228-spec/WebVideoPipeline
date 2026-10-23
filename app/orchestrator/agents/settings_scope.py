"""Правка настроек проекта меняет только то, что назвал человек.

«Во всех проектах поменяй модель картинок на дефолтную»: планировщик переписывал в каждом
проекте все настройки по умолчанию — название, тему, формат кадра, героя, разрешение и
модель видео. Карточка показывала чужие правки, а применение затёрло бы названия.
Здесь из шагов ``project.patch_settings`` убираются ключи, о которых в просьбе нет ни
слова. Название и тему в правке нескольких проектов сразу без слов о них не трогаем никогда.
Если в просьбе не назван ни один ключ, у одного проекта шаг остаётся как есть.
"""

from __future__ import annotations

import re
from typing import Any

from app.orchestrator.contracts import PlanStep

SETTINGS_ACTIONS: frozenset[str] = frozenset({"project.patch_settings", "project.patch_settings_costly"})
SETTINGS_KEYS: tuple[str, ...] = (
    "title",
    "topic",
    "hero_mode",
    "image_generator",
    "image_resolution",
    "video_generator",
    "video_resolution",
    "aspect_ratio",
)

_IMG = re.compile(r"картин|изображ|фото|кадр|image|img|png")
_VID = re.compile(r"видео|ролик|анимац|video|клип")
_MODEL = re.compile(r"модел|генератор|нейросет")
_RES = re.compile(
    r"разрешен|качеств|(?<![\d.])\d+\s*[kк](?![a-zа-я])|\d{3,4}\s*[pр](?![a-zа-я])|"
    r"(?<![a-zа-я])(?:hd|хд|fhd|full\s*hd|фулл)(?![a-zа-я])"
)
_ASPECT = re.compile(r"формат|соотношен|вертикал|горизонт|квадрат|\d{1,2}\s*[:x×]\s*\d{1,2}|ориентац")
_HERO = re.compile(r"геро|персонаж")
_TITLE = re.compile(r"назван|переимен|назови|имя\b")
_TOPIC = re.compile(r"(?<![a-zа-я])тем[аыуео]")


def _norm(text: str) -> str:
    return " ".join((text or "").lower().replace("ё", "е").split())


def _value_named(value: Any, norm: str) -> bool:
    """Значение названо прямо: «kling», «2k», «flux»."""
    from app.services.action_layer.planner_rules import _generator_named, _resolution_named

    raw = str(value or "").strip().lower()
    if not raw:
        return False
    if re.fullmatch(r"\d+[kp]", raw):
        return _resolution_named(raw, norm)
    return _generator_named(raw, norm) and any(
        part in norm for part in re.split(r"[_\-]", raw) if len(part) >= 3
    )


def named_keys(goal: str, params: dict[str, Any]) -> set[str]:
    """Ключи настроек, о которых в просьбе есть слова (или названо само значение)."""
    norm = _norm(goal)
    img, vid = bool(_IMG.search(norm)), bool(_VID.search(norm))
    # «Смени модель» без «картинок» и «видео» — любая из двух моделей.
    img_side = img or not vid
    vid_side = vid or not img
    out: set[str] = set()
    if _MODEL.search(norm):
        if img_side:
            out.add("image_generator")
        if vid_side:
            out.add("video_generator")
    if _RES.search(norm):
        if img_side:
            out.add("image_resolution")
        if vid_side:
            out.add("video_resolution")
    if _ASPECT.search(norm):
        out.add("aspect_ratio")
    if _HERO.search(norm):
        out.add("hero_mode")
    if _TITLE.search(norm):
        out.add("title")
    if _TOPIC.search(norm):
        out.add("topic")
    for key in SETTINGS_KEYS:
        if (
            key in params
            and key not in out
            and key.endswith(("generator", "resolution"))
            and _value_named(params[key], norm)
        ):
            out.add(key)
    return out


def scope_settings(plan: list[PlanStep], goal: str) -> list[PlanStep]:
    """Шаги настроек проекта — только с названными ключами; остальное без изменений."""
    steps = [step for step in plan if step.action in SETTINGS_ACTIONS]
    if not steps or not (goal or "").strip():
        return plan
    many = len({step.params.get("project_id") for step in steps}) > 1
    out: list[PlanStep] = []
    for step in plan:
        if step.action not in SETTINGS_ACTIONS:
            out.append(step)
            continue
        params = dict(step.params)
        present = [key for key in SETTINGS_KEYS if key in params]
        keep = named_keys(goal, params)
        kept = [key for key in present if key in keep]
        if not kept:
            # Ни одного названного ключа: смысл просьбы не ясен из слов — решает проверка
            # шага и карточка. Но название и тему в массовой правке не переписываем.
            kept = [key for key in present if not (many and key in ("title", "topic"))]
        params = {key: value for key, value in params.items() if key not in SETTINGS_KEYS or key in kept}
        out.append(step if params == dict(step.params) else step.model_copy(update={"params": params}))
    return out
