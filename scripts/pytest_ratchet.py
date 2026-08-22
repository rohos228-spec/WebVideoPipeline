#!/usr/bin/env python3
"""Храповик по падениям тестов: красное не растёт.

Зачем не «просто зелёный гейт». На 2026-08-22 полный прогон даёт десятки
падений, и почти все — не регрессии: промт-библиотека намеренно вне git
(`.gitignore`: `prompts/*`), поэтому на любой машине, кроме владельческой,
часть тестов падает на отсутствии ДАННЫХ. Гейт «должно быть зелено» в такой
ситуации красный всегда, а вечно красный гейт отключают в первый же день.

Храповик решает ровно ту задачу, ради которой гейт заводится: не дать
красноте вырасти. Список известных падений лежит в
`.claude/baselines/pytest-failures.txt`; проверка падает, только если
появилось падение, которого там нет.

Использование::

    python3 scripts/pytest_ratchet.py                # прогнать и сверить
    python3 scripts/pytest_ratchet.py --update       # перезаписать базу
    python3 scripts/pytest_ratchet.py -- -k queue    # свои аргументы pytest

Коды возврата: 0 — новых падений нет; 1 — есть; 2 — сам pytest не запустился.

Починили тест — база не обновляется сама: скрипт печатает, что стало
зелёным, и просит прогнать `--update`. Это осознанно: сужение базы должно
быть видно в диффе, иначе храповик тихо проворачивается назад.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BASELINE = REPO_ROOT / ".claude" / "baselines" / "pytest-failures.txt"

_FAILED_RE = re.compile(r"^(?:FAILED|ERROR)\s+(\S+)", re.MULTILINE)


def _load_baseline() -> set[str]:
    if not BASELINE.is_file():
        return set()
    out: set[str] = set()
    for line in BASELINE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.add(line)
    return out


def _write_baseline(failures: set[str]) -> None:
    BASELINE.parent.mkdir(parents=True, exist_ok=True)
    header = (
        "# Известные падения тестов. Гейт (scripts/pytest_ratchet.py) красный,\n"
        "# только если появилось падение, которого здесь нет.\n"
        "#\n"
        "# Большая часть строк — НЕ баги кода: промт-библиотека вне git\n"
        "# (.gitignore: prompts/*), и без неё тесты падают на отсутствии\n"
        "# данных. Проверить: python3 scripts/check_prompts.py\n"
        "#\n"
        "# Сузить базу: почините тест и прогоните\n"
        "#   python3 scripts/pytest_ratchet.py --update\n"
        "# Изменение должно быть видно в диффе — сам скрипт базу не правит.\n"
    )
    BASELINE.write_text(header + "\n".join(sorted(failures)) + "\n", encoding="utf-8")


def _run_pytest(extra: list[str]) -> tuple[int, str]:
    cmd = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-rf", *extra]
    proc = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True)
    return proc.returncode, proc.stdout + proc.stderr


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--update", action="store_true", help="перезаписать базу текущими падениями")
    parser.add_argument("pytest_args", nargs="*", help="аргументы для pytest (после --)")
    args = parser.parse_args()

    code, output = _run_pytest(args.pytest_args)
    # 0 — всё прошло, 1 — есть падения. Остальное (2 — прерывание, 4 —
    # ошибка использования, 5 — тестов не найдено) значит, что прогон не
    # состоялся: молча считать это «падений нет» нельзя.
    if code not in (0, 1):
        sys.stderr.write(output[-4000:])
        sys.stderr.write(f"\npytest_ratchet: pytest не отработал (код {code})\n")
        return 2

    failures = set(_FAILED_RE.findall(output))

    if args.update:
        _write_baseline(failures)
        print(f"pytest_ratchet: база обновлена — {len(failures)} известных падений")
        try:
            shown = BASELINE.relative_to(REPO_ROOT)
        except ValueError:  # база вне репозитория (тесты подменяют путь)
            shown = BASELINE
        print(f"  {shown}")
        return 0

    baseline = _load_baseline()
    new = sorted(failures - baseline)
    fixed = sorted(baseline - failures)

    if fixed:
        print(f"pytest_ratchet: стало зелёным ({len(fixed)}) — сузьте базу через --update:")
        for nodeid in fixed:
            print(f"    {nodeid}")

    if not new:
        print(f"pytest_ratchet: новых падений нет ({len(failures)} известных)")
        return 0

    print(f"\npytest_ratchet: НОВЫЕ падения ({len(new)}):")
    for nodeid in new:
        print(f"    {nodeid}")
    print(
        "\nЕсли это падение на отсутствии промтов, а не регрессия — "
        "проверьте: python3 scripts/check_prompts.py"
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
