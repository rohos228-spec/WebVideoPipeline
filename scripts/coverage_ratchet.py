#!/usr/bin/env python3
"""Храповик покрытия по файлам: покрытие не падает.

Почему свой, а не общий `~/.agents/bin/coverage-ratchet.py`. Тот читает
istanbul-овский `coverage-summary.json`, который пишут vitest и jest; у
`pytest-cov` формат другой (`--cov-report=json`), и метрик там три, а не
четыре — `functions` coverage.py не считает вовсе. Натягивать один формат на
другой значило бы конвертировать отчёт ради совместимости с чужой схемой.

Семантика — та же, что у всех храповиков gauntlet:

* просадка покрытия любого файла из базы — красный;
* файл пропал из отчёта — красный. Так покрытие и теряют: файл переименовали
  или он перестал загружаться тестами, а цифры «не просели», потому что их
  больше нет;
* рост — база переписывается сама и просит закоммитить: сужение базы должно
  быть видно в диффе, иначе храповик тихо проворачивается назад;
* новый файл попадает в базу при следующем росте или по `--update`.

Использование::

    # покрытие собирается прогоном суиты, см. .claude/verify.json
    python3 scripts/coverage_ratchet.py
    python3 scripts/coverage_ratchet.py --update      # переписать базу

Коды возврата: 0 — просадки нет; 1 — есть; 2 — отчёта нет.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "coverage" / "coverage.json"
BASELINE = ROOT / ".claude" / "baselines" / "coverage.json"

#: Насколько процента позволено «дрогнуть» без объявления регрессии. Не для
#: снисходительности: coverage.py округляет, и файл на 3 строки прыгает на
#: треть процента от перестановки. Настоящая просадка всегда больше.
EPSILON = 0.05


def read_report(path: Path) -> dict[str, float]:
    """Отчёт pytest-cov → {путь относительно корня: процент строк}."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    out: dict[str, float] = {}
    for file_path, data in (raw.get("files") or {}).items():
        rel = Path(file_path)
        if rel.is_absolute():
            try:
                rel = rel.relative_to(ROOT)
            except ValueError:
                rel = Path(rel.name)
        out[rel.as_posix()] = float(data["summary"]["percent_covered"])
    return out


def read_baseline(path: Path) -> dict[str, float]:
    if not path.is_file():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {k: float(v) for k, v in raw.items() if not k.startswith("_")}


def write_baseline(path: Path, files: dict[str, float]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "_comment": (
            "Храповик покрытия по файлам (scripts/coverage_ratchet.py). Просадка "
            "или пропажа файла — красный; рост подтягивается сам. Проценты — "
            "покрытие СТРОК по отчёту pytest-cov."
        ),
        **{k: round(v, 2) for k, v in sorted(files.items())},
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Храповик покрытия")
    parser.add_argument("--report", default=str(REPORT))
    parser.add_argument("--baseline", default=str(BASELINE))
    parser.add_argument("--update", action="store_true", help="переписать базу текущим отчётом")
    args = parser.parse_args(argv)

    report_path = Path(args.report)
    if not report_path.is_file():
        print(
            f"нет отчёта {report_path}. Собрать: .venv/bin/python -m pytest "
            f"--cov=app --cov-report=json:{report_path}",
            file=sys.stderr,
        )
        return 2

    current = read_report(report_path)
    if not current:
        print(f"{report_path}: в отчёте ни одного файла", file=sys.stderr)
        return 2

    baseline_path = Path(args.baseline)
    if args.update or not baseline_path.is_file():
        write_baseline(baseline_path, current)
        print(f"база покрытия записана: {len(current)} файлов → {baseline_path}")
        return 0

    base = read_baseline(baseline_path)
    dropped = [
        (name, base[name], current[name])
        for name in sorted(base)
        if name in current and current[name] + EPSILON < base[name]
    ]
    vanished = sorted(name for name in base if name not in current)

    if dropped:
        print("покрытие просело:")
        for name, was, now in dropped:
            print(f"  {name}: {was:.2f}% → {now:.2f}%")
    if vanished:
        print("файлы пропали из отчёта (переименованы или перестали загружаться тестами):")
        for name in vanished:
            print(f"  {name}")
    if dropped or vanished:
        print("\nЕсли изменение осознанное: python3 scripts/coverage_ratchet.py --update")
        return 1

    grown = {n: c for n, c in current.items() if c > base.get(n, -1.0) + EPSILON}
    new_files = sorted(set(current) - set(base))
    if grown or new_files:
        write_baseline(baseline_path, {**base, **current})
        parts = []
        if grown:
            parts.append(f"выросло файлов: {len(grown)}")
        if new_files:
            parts.append(f"новых: {len(new_files)}")
        print(f"база подтянута ({', '.join(parts)}) — закоммитить {baseline_path.name}")
        return 0

    print(f"покрытие держится: {len(base)} файлов в базе, просадки нет")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
