"""Оценка рисков запуска шага пайплайна (reset downstream, busy, user_stop, auto_chain)."""

from __future__ import annotations

from typing import Any

EARLY_STEPS: frozenset[str] = frozenset({"plan", "script", "split"})
STOP_HINT: str = "Остановить можно кнопкой ⏹ Стоп."

_TITLES: dict[str, str] = {
    "plan": "План",
    "script": "Сценарий",
    "split": "Разбивка",
    "scene_d": "Дизайн сцен",
    "scene_design": "Дизайн сцен",
    "sd_agent": "Дизайн сцен",
    "sd_assemble": "Сборка сцен",
    "hero": "Герои",
    "items": "Предметы",
    "image_prompts": "Промты картинок",
    "images": "Картинки",
    "img": "Картинки",
    "animation_prompts": "Промты анимаций",
    "video": "Видео",
    "videos": "Видео",
    "audio": "Озвучка",
    "music": "Музыка",
    "sfx_plan": "План SFX",
    "sfx_gen": "Генерация SFX",
    "assemble": "Монтаж",
    "publish": "Публикация",
}

_ORDER: tuple[str, ...] = (
    "plan",
    "script",
    "split",
    "scene_design",
    "sd_agent",
    "sd_assemble",
    "hero",
    "items",
    "enrich_1",
    "enrich_2",
    "enrich_3",
    "enrich_4",
    "enrich_5",
    "image_prompts",
    "images",
    "img",
    "animation_prompts",
    "video",
    "videos",
    "audio",
    "music",
    "sfx_plan",
    "sfx_gen",
    "assemble",
    "publish",
)


def step_title(step_code: str | None) -> str:
    if not step_code:
        return ""
    return _TITLES.get(step_code, str(step_code))


def is_running(status: str | None) -> bool:
    if not status:
        return False
    s = str(status).strip()
    return s.endswith("_running") or s.startswith("generating_") or s in {
        "planning",
        "scripting",
        "splitting",
        "scene_designing",
        "scene_assembling",
        "assembling",
        "publishing",
        "running",
    }


def user_stopped(meta: Any) -> bool:
    if not isinstance(meta, dict):
        return False
    return bool(meta.get("user_stop"))


def launch_risks(
    *,
    step_code: str,
    status: str | None = None,
    auto_mode: bool = False,
    meta: dict[str, Any] | None = None,
    node_runs: list[dict[str, Any]] | None = None,
    **kwargs: Any,
) -> list[dict[str, str]]:
    risks: list[dict[str, str]] = []

    # Downstream reset risk: only checked for early steps
    if step_code in EARLY_STEPS and node_runs:
        try:
            curr_idx = _ORDER.index(step_code)
        except ValueError:
            curr_idx = -1
        if curr_idx >= 0:
            downstream: list[str] = []
            for nr in node_runs:
                if nr.get("status") == "done":
                    nt = nr.get("node_type")
                    if not nt:
                        continue
                    try:
                        n_idx = _ORDER.index(nt)
                    except ValueError:
                        n_idx = -1
                    if n_idx > curr_idx:
                        title = step_title(nt)
                        if title and title not in downstream:
                            downstream.append(title)
            if downstream:
                risks.append({
                    "code": "reset_downstream",
                    "text": f"Перезапуск сбросит готовые шаги: {', '.join(downstream)}.",
                })

    if is_running(status):
        risks.append({
            "code": "busy",
            "text": "Проект уже выполняет другой шаг.",
        })

    if user_stopped(meta):
        risks.append({
            "code": "user_stop",
            "text": "Проект был остановлен пользователем.",
        })

    if auto_mode:
        risks.append({
            "code": "auto_chain",
            "text": "Включено автопродвижение: после этого шага цепочка продолжится автоматически.",
        })

    return risks
