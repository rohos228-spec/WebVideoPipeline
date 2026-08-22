#!/usr/bin/env python3
"""Прогнать тесты, относящиеся к изменённым файлам.

У pytest нет аналога `jest --findRelatedTests`, а полная суита идёт ~4 мин —
в бюджет коммита не влезает. Эвристика простая и предсказуемая:

* изменён `tests/test_x.py`      → гоняем его;
* изменён `app/.../<модуль>.py`  → гоняем `tests/test_<модуль>.py`, если
  такой файл есть, плюс файлы вида `tests/test_<модуль>_*.py`.

Ничего не нашли — выходим с 0: это не повод блокировать коммит, полная
суита всё равно на ярусе push (с храповиком, см. `pytest_ratchet.py`).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TESTS_DIR = REPO_ROOT / "tests"


def _tests_for(changed: str) -> set[Path]:
    path = Path(changed)
    if path.suffix != ".py":
        return set()

    if path.parts and path.parts[0] == "tests":
        candidate = REPO_ROOT / path
        return {candidate} if candidate.is_file() else set()

    stem = path.stem
    if stem in {"__init__", "conftest"}:
        return set()

    found: set[Path] = set()
    exact = TESTS_DIR / f"test_{stem}.py"
    if exact.is_file():
        found.add(exact)
    found.update(p for p in TESTS_DIR.glob(f"test_{stem}_*.py") if p.is_file())
    return found


def main(argv: list[str]) -> int:
    targets: set[Path] = set()
    for arg in argv:
        targets |= _tests_for(arg)

    if not targets:
        print("pytest_touched: подходящих тестов не нашлось — пропускаю")
        return 0

    rel = sorted(str(p.relative_to(REPO_ROOT)) for p in targets)
    print(f"pytest_touched: {len(rel)} файл(ов) — {', '.join(rel)}")
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *rel],
        cwd=REPO_ROOT,
    )
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
