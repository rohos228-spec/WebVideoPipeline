#!/usr/bin/env python3
"""Изменённые строки обязаны быть покрыты тестами.

Зачем он, когда есть храповик покрытия. Храповик ловит ПРОСАДКУ существующих
файлов; новый файл входит в базу со своими нулями и живёт так вечно. Этот гейт
смотрит только на дифф: строка, которую добавили или изменили, обязана
исполняться хотя бы одним тестом.

Почему не общий `~/.agents/bin/diff-coverage.py`: тот фильтрует дифф по
`.ts`/`.tsx` и несёт список исключений, списанный с `jest.config` бэкенда
PelmenVPN. Механика одинаковая (`git diff -U0` ∩ `DA`-записи lcov), а
предметная часть — своя.

Использование::

    python3 scripts/diff_coverage.py --lcov coverage/lcov.info
    python3 scripts/diff_coverage.py --lcov coverage/lcov.info --base origin/main

Семантика:

* база — `@{u}`, без upstream — `origin/main`; сравнивается РАБОЧЕЕ ДЕРЕВО с
  базой, то есть гейт видит и коммиты, и незакоммиченное — ровно то, что
  уедет или вот-вот уедет;
* «покрываемой» считается строка, которая есть в `DA`-записях lcov: пустые
  строки, docstring-и и `def` без тела туда не попадают, и мы их не требуем;
* файл, изменённый, но ОТСУТСТВУЮЩИЙ в lcov и не исключённый, — красный: его
  не загрузил ни один тест, это и есть «новый модуль без тестов»;
* удалённые файлы и не-`.py` игнорируются.

Коды возврата: 0 — чисто; 1 — есть непокрытые изменённые строки; 2 — ошибка.
"""

from __future__ import annotations

import argparse
import fnmatch
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: Что не требуем покрывать. Список короткий намеренно: каждая строка здесь —
#: код, за который никто не отвечает.
DEFAULT_EXCLUDES = (
    "tests/*",
    "scripts/*",  # инструменты оператора; свои тесты у них есть, покрытия не собираем
    "migrations/*",  # ревизии проверяются отдельным шагом на живой схеме
    "app/__init__.py",
    "*/__init__.py",
    "app/main.py",  # точка входа: поднимает процесс целиком
)


def _git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True)


def _base_ref(explicit: str) -> str:
    if explicit:
        return explicit
    try:
        return _git("rev-parse", "--abbrev-ref", "@{u}").strip()
    except subprocess.CalledProcessError:
        pass
    for candidate in ("origin/main", "origin/master", "main", "master"):
        try:
            _git("rev-parse", "--verify", candidate)
            return candidate
        except subprocess.CalledProcessError:
            continue
    raise SystemExit("не нашёл базу для сравнения: ни @{u}, ни origin/main")


_HUNK = re.compile(r"^@@ -\S+ \+(\d+)(?:,(\d+))? @@")


def changed_lines(base: str) -> dict[str, set[int]]:
    """{файл: номера добавленных/изменённых строк} для рабочего дерева."""
    diff = _git("diff", "-U0", base, "--", "*.py")
    out: dict[str, set[int]] = defaultdict(set)
    current = ""
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            current = line[6:].strip()
            continue
        if line.startswith("+++ /dev/null"):
            current = ""
            continue
        if not current:
            continue
        m = _HUNK.match(line)
        if m:
            start = int(m.group(1))
            count = int(m.group(2) or 1)
            out[current].update(range(start, start + count))
    return {k: v for k, v in out.items() if v}


def covered_lines(lcov_path: Path) -> dict[str, set[int]]:
    """{файл: строки, исполненные хотя бы раз} из lcov."""
    out: dict[str, set[int]] = defaultdict(set)
    current = ""
    for raw in lcov_path.read_text(encoding="utf-8").splitlines():
        if raw.startswith("SF:"):
            path = Path(raw[3:].strip())
            if path.is_absolute():
                try:
                    path = path.relative_to(ROOT)
                except ValueError:
                    pass
            current = path.as_posix()
        elif raw.startswith("DA:") and current:
            num, _, hits = raw[3:].partition(",")
            if int(hits or 0) > 0:
                out[current].add(int(num))
    return out


def coverable_lines(lcov_path: Path) -> dict[str, set[int]]:
    """{файл: строки, которые вообще считаются покрываемыми}."""
    out: dict[str, set[int]] = defaultdict(set)
    current = ""
    for raw in lcov_path.read_text(encoding="utf-8").splitlines():
        if raw.startswith("SF:"):
            path = Path(raw[3:].strip())
            if path.is_absolute():
                try:
                    path = path.relative_to(ROOT)
                except ValueError:
                    pass
            current = path.as_posix()
        elif raw.startswith("DA:") and current:
            out[current].add(int(raw[3:].split(",")[0]))
    return out


def excluded(path: str, patterns: tuple[str, ...]) -> bool:
    return any(fnmatch.fnmatch(path, p) for p in patterns)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Diff-coverage для Python")
    parser.add_argument("--lcov", default="coverage/lcov.info")
    parser.add_argument("--base", default="")
    parser.add_argument("--exclude", action="append", default=[])
    args = parser.parse_args(argv)

    lcov_path = Path(args.lcov)
    if not lcov_path.is_absolute():
        lcov_path = ROOT / lcov_path
    if not lcov_path.is_file():
        print(
            f"нет {lcov_path}. Собрать: .venv/bin/python -m pytest --cov=app --cov-report=lcov:{args.lcov}",
            file=sys.stderr,
        )
        return 2

    patterns = DEFAULT_EXCLUDES + tuple(args.exclude)
    base = _base_ref(args.base)
    changed = changed_lines(base)
    if not changed:
        print(f"против {base}: изменённых .py нет")
        return 0

    covered = covered_lines(lcov_path)
    coverable = coverable_lines(lcov_path)

    problems: list[str] = []
    checked = 0
    for path, lines in sorted(changed.items()):
        if excluded(path, patterns):
            continue
        if not (ROOT / path).is_file():
            continue  # удалён
        if path not in coverable:
            problems.append(f"{path}: нет в отчёте покрытия — его не загрузил ни один тест")
            continue
        # Требуем покрытия только с тех строк, которые вообще покрываемы:
        # пустые строки, docstring-и и сигнатуры в DA не попадают.
        want = lines & coverable[path]
        checked += len(want)
        missed = sorted(want - covered.get(path, set()))
        if missed:
            shown = ", ".join(str(n) for n in missed[:15])
            tail = f" … и ещё {len(missed) - 15}" if len(missed) > 15 else ""
            problems.append(f"{path}: не покрыты строки {shown}{tail}")

    if problems:
        print(f"против {base}: изменённый код без тестов")
        for p in problems:
            print(f"  {p}")
        return 1

    print(f"против {base}: изменённые строки покрыты ({checked} покрываемых строк)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
