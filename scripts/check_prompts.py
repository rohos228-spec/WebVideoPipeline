#!/usr/bin/env python3
"""Проверка промт-библиотеки: что из `prompts/` есть, чего не хватает.

Зачем. Библиотека мастер-промтов намеренно не хранится в git
(`.gitignore`: `prompts/*`, исключения — `scene_design/` и
`05_excel_gpt/sd_*.md`). На свежей машине конвейер поэтому падает
`FileNotFoundError` на первом же шаге, а десятки тестов краснеют на
отсутствии данных, а не на коде. Скрипт отвечает на вопрос «это у меня
сломан код или просто нет промтов» за одну команду.

Использование::

    python3 scripts/check_prompts.py           # человекочитаемо
    python3 scripts/check_prompts.py --json    # для CI/агентов

Коды возврата: 0 — обязательный минимум на месте; 1 — чего-то не хватает.
Опциональные блоки (blocks v2, check_operator) на код возврата не влияют,
если для них нет ни одного файла: это отдельные фичи, а не ядро конвейера.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

REPO_ROOT = Path(__file__).resolve().parent.parent
PROMPTS_ROOT = REPO_ROOT / "prompts"

# step_code → папка. Держим локальную копию, а не импорт из app.services:
# скрипт должен работать до установки зависимостей проекта.
STEP_FOLDERS: dict[str, str] = {
    "plan": "01_plan",
    "script": "02_script",
    "split": "03_razbivka",
    "hero": "04_hero",
    "hero_style": "04_hero_style",
    "items": "04b_items",
    "enrich_1": "05a_enrich_1",
    "enrich_2": "05b_enrich_2",
    "enrich_3": "05c_enrich_3",
    "enrich_4": "05d_enrich_4",
    "enrich_5": "05e_enrich_5",
    "excel_gpt": "05_excel_gpt",
    "img_pr": "05_image_prompts",
    "anim_pr": "07_animation",
    "scene_d": "scene_design",
}

# Без этих `default.md` конвейер не проходит соответствующий шаг.
REQUIRED_STEPS: tuple[str, ...] = (
    "plan",
    "script",
    "split",
    "hero",
    "img_pr",
    "anim_pr",
)

# Есть в git — если пусто, значит чекаут битый, а не «промты не завезли».
TRACKED_DIRS: tuple[tuple[str, str], ...] = (
    ("prompts/scene_design", "агенты дизайна сцен (в git)"),
    ("prompts/05_excel_gpt", "sd_*.md — веер scene-агентов (в git)"),
)

# Проверки нод (`check_analysis.list_check_operator_steps`). Имена папок —
# результат `resolve_check_operator_step`, а не step_code.
CHECK_OPERATOR_STEPS: tuple[str, ...] = (
    "plan",
    "script",
    "split",
    "hero",
    "items",
    "excel_gpt",
    "image_prompts",
    "images",
    "animation_prompts",
    "videos",
    "audio",
    "assemble",
    "storage",
    "publish",
)


def _has_md(directory: Path) -> bool:
    return directory.is_dir() and any(directory.glob("*.md"))


def collect() -> dict[str, object]:
    missing_required: list[str] = []
    present_required: list[str] = []
    variants_only: list[str] = []
    for step in REQUIRED_STEPS:
        folder = PROMPTS_ROOT / STEP_FOLDERS[step]
        rel = f"prompts/{STEP_FOLDERS[step]}/default.md"
        if (REPO_ROOT / rel).is_file():
            present_required.append(rel)
            continue
        others = sorted(p.name for p in folder.glob("*.md")) if folder.is_dir() else []
        if others:
            # Шаг проедет, только если проект явно выбрал вариант
            # (prompt_overrides / слот Studio); иначе резолв уходит в
            # DEFAULT_NAME и падает FileNotFoundError.
            variants_only.append(f"prompts/{STEP_FOLDERS[step]}/ → {', '.join(others)}")
        missing_required.append(rel)

    missing_tracked = [rel for rel, _why in TRACKED_DIRS if not _has_md(REPO_ROOT / rel)]

    # scene_design живёт именованными файлами агентов (characters.md, world.md,
    # …), `default.md` там не бывает — из опциональной проверки исключён.
    optional_steps = [
        s for s in STEP_FOLDERS if s not in REQUIRED_STEPS and s not in ("scene_d", "scene_asm")
    ]
    missing_optional = [
        f"prompts/{STEP_FOLDERS[s]}/default.md"
        for s in optional_steps
        if not (REPO_ROOT / "prompts" / STEP_FOLDERS[s] / "default.md").is_file()
    ]

    blocks_v2 = {name: (PROMPTS_ROOT / name).is_dir() for name in ("steps", "blocks", "styles")}

    check_root = PROMPTS_ROOT / "check_operator"
    checks_present = [s for s in CHECK_OPERATOR_STEPS if (check_root / s / "default.md").is_file()]
    checks_missing = [s for s in CHECK_OPERATOR_STEPS if s not in checks_present]

    return {
        "prompts_root": str(PROMPTS_ROOT),
        "prompts_root_exists": PROMPTS_ROOT.is_dir(),
        "required_present": present_required,
        "required_missing": missing_required,
        "required_variants_only": variants_only,
        "tracked_missing": missing_tracked,
        "optional_missing": missing_optional,
        "blocks_v2": blocks_v2,
        "check_operator_present": checks_present,
        "check_operator_missing": checks_missing,
        "ok": not missing_required and not missing_tracked,
    }


def render(report: dict[str, object]) -> None:
    out = sys.stdout.write
    out(f"Промт-библиотека: {report['prompts_root']}\n\n")

    if not report["prompts_root_exists"]:
        out("  папки prompts/ нет вообще — конвейер не стартует\n\n")

    missing_required = report["required_missing"]
    assert isinstance(missing_required, list)
    if missing_required:
        out("ОТСУТСТВУЮТ — конвейер не стартует:\n")
        for rel in missing_required:
            out(f"    {rel}\n")
        out("\n")
        variants_only = report["required_variants_only"]
        assert isinstance(variants_only, list)
        if variants_only:
            out("  (папка не пустая, но default.md нет — шаг проедет только\n")
            out("   если проект явно выбрал вариант в Studio):\n")
            for line in variants_only:
                out(f"    {line}\n")
            out("\n")
    else:
        out("Обязательный минимум на месте (plan/script/split/hero/img_pr/anim_pr).\n\n")

    tracked_missing = report["tracked_missing"]
    assert isinstance(tracked_missing, list)
    if tracked_missing:
        out("ОТСУТСТВУЮТ, хотя лежат в git — проверь чекаут:\n")
        for rel in tracked_missing:
            out(f"    {rel}\n")
        out("\n")

    blocks = report["blocks_v2"]
    assert isinstance(blocks, dict)
    if not any(blocks.values()):
        out("blocks v2 (prompts/steps, blocks, styles): нет ни одной папки.\n")
        out("    Компонентная сборка промтов недоступна; тесты prompt_composer\n")
        out("    и prompt_step_presets будут падать на данных, а не на коде.\n\n")
    elif not all(blocks.values()):
        gone = ", ".join(f"prompts/{k}" for k, v in blocks.items() if not v)
        out(f"blocks v2: частично — нет {gone}\n\n")

    checks_missing = report["check_operator_missing"]
    checks_present = report["check_operator_present"]
    assert isinstance(checks_missing, list) and isinstance(checks_present, list)
    total = len(checks_present) + len(checks_missing)
    if checks_missing:
        out(f"prompts/check_operator: {len(checks_present)} из {total} шагов.\n")
        out(f"    Нет: {', '.join(checks_missing)}\n")
        out("    Без них проверки нод не настроены → auto_review отдаёт skipped\n")
        out("    (fail-closed, stage-0 п.2), а не тихий auto-approve.\n\n")

    optional_missing = report["optional_missing"]
    assert isinstance(optional_missing, list)
    if optional_missing:
        out(f"Опциональные шаги без default.md: {len(optional_missing)}\n")
        for rel in optional_missing:
            out(f"    {rel}\n")
        out("\n")

    if report["ok"]:
        out("ИТОГ: ядро на месте.\n")
    else:
        out("ИТОГ: библиотека неполная — см. HANDOVER.md §2.5.\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="машиночитаемый вывод")
    args = parser.parse_args()

    report = collect()
    if args.json:
        json.dump(report, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    else:
        render(report)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
