"""Гейт: библиотека промтов не расходится с `docs/PROMPT_CONTRACT.md`.

Контракт («ответ — JSON apply-ops, Excel не вход и не выход») существовал
только в документации, и проверять его было нечем. Так после пивота 171 промт
остался написанным под удалённый режим: дефолтный промт шага доработки просил
модель «приложить обновлённый project.xlsx» и отдавал ей семь нерешённых
`{{BLOCK:…}}` буквально. Снаружи — «шаг вернул мусор», и никто не понимал
почему.

Гейт бежит по `prompts/` на диске (это то, что импортируется в базу на первом
старте) и пропускается там, где библиотеки нет — в CI её нет намеренно. То
есть держит его владелец, локально, на ярусе хода (`.claude/verify.json`).
На сервере ту же проверку делает предупреждение на старте по базе.

Первый набор случаев — про сам детектор: он обязан пропускать законные
упоминания Excel (запреты, метафоры, карты миграции) и ловить инструкции.
Без этого гейт либо шумит и его отключают, либо молчит и бесполезен.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.services.prompt_contract import violations
from app.services.prompt_library import STEP_FOLDERS

ROOT = Path(__file__).resolve().parents[1]
PROMPTS = ROOT / "prompts"


# ── детектор ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "line",
    [
        "Куда пишу: обязательно обновлённый `project.xlsx` целиком, приложенный файлом в ответ.",
        "Откуда читаю: приложенный project.xlsx, лист «план».",
        "14. Сохранить результат как новый XLSX-файл.",
        "1. Открыть Excel-файл.",
        "# Лист: план",
        "{{BLOCK:enrich_role}}",
        "{{VAR:EXCEL_GPT_TASK}}",
    ],
)
def test_detector_catches_instructions(line: str) -> None:
    assert violations(line), f"инструкция прошла мимо гейта: {line!r}"


@pytest.mark.parametrize(
    "line",
    [
        "Не пиши `# Лист:`, TSV, `@row=`, не проси project.xlsx.",
        "Источник правды = База (вложение `db_frames.json`), не Excel и не TSV.",
        "# ПИШЕТ В БАЗУ (apply-ops), НЕ в Excel напрямую.",
        "4. Сцена ≠ «одна колонка Excel». Режь по смыслу.",
        "## 2. Карта: было Excel → поле в apply-ops",
        "- `xlsx_valid` — данные проекта читаются, структура не разрушена.",
        "# (PLAN_XLSX_OUTPUT_FOOTER) — здесь только замысел.",
        "Ответ в виде TSV / «# Лист:» / приложенного .xlsx вместо apply-ops.",
        "{{VAR:PROJECT_TOPIC}}",
    ],
)
def test_detector_allows_legitimate_mentions(line: str) -> None:
    assert not violations(line), f"ложное срабатывание: {line!r} → {violations(line)}"


# ── библиотека ───────────────────────────────────────────────────────────


def _library_files() -> list[Path]:
    if not PROMPTS.is_dir():
        return []
    files: list[Path] = []
    for folder in set(STEP_FOLDERS.values()):
        d = PROMPTS / folder
        if d.is_dir():
            files += [f for f in d.rglob("*.md") if ".history" not in f.parts]
    return sorted(files)


@pytest.mark.skipif(not PROMPTS.is_dir(), reason="prompts/ нет — библиотека вне git")
def test_library_matches_the_contract() -> None:
    files = _library_files()
    assert len(files) > 30, f"нашлось всего {len(files)} промтов — библиотека неполная или путь не тот"

    report: list[str] = []
    for f in files:
        bad = violations(f.read_text(encoding="utf-8", errors="replace"))
        if bad:
            rel = f.relative_to(ROOT)
            report.append(f"  {rel} ({len(bad)}):")
            report.extend(f"      {v}" for v in bad[:4])
    assert not report, (
        "промты расходятся с docs/PROMPT_CONTRACT.md — модель получит инструкцию, "
        "которую нельзя выполнить:\n" + "\n".join(report)
    )


@pytest.mark.skipif(not PROMPTS.is_dir(), reason="prompts/ нет — библиотека вне git")
def test_retired_agents_are_out_of_the_library() -> None:
    """Отставленные xlsx-агенты не лежат там, откуда импортируются."""
    for folder in set(STEP_FOLDERS.values()):
        d = PROMPTS / folder
        if not d.is_dir():
            continue
        names = [f.name for f in d.glob("*.md")]
        stale = [n for n in names if "заполнение эксель" in n or "agent_54_59" in n or "07.07" in n]
        assert not stale, f"{folder}: отставленные агенты снова в библиотеке: {stale}"
