#!/usr/bin/env python3
"""Repair-rate контрактной политики — метрика приёмки этапа 5.

Читает data/videos/*/llm_metrics.jsonl (пишет app/contracts/policy.py:
одна строка = одна логическая единица работы; транспортные ретраи chat(),
дробление 1→2→4 и continuation repair'ом НЕ считаются — определение
tasks D.5). Порог приёмки: repair-rate ≤ 10% и 0 исчерпаний (ok=false).

Запуск:  .venv/bin/python scripts/llm_repair_rate.py [DATA_DIR]
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path


def main() -> int:
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("data")
    files = sorted(root.glob("**/llm_metrics.jsonl"))
    if not files:
        print(f"нет llm_metrics.jsonl под {root} — прогонов с политикой не было")
        return 1

    by_label: dict[str, dict[str, int]] = defaultdict(
        lambda: {"units": 0, "repaired": 0, "attempts": 0, "exhausted": 0}
    )
    for path in files:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            key = str(row.get("contract") or row.get("label") or "?")
            agg = by_label[key]
            agg["units"] += 1
            agg["attempts"] += int(row.get("attempts") or 1)
            if int(row.get("repairs") or 0) > 0:
                agg["repaired"] += 1
            if not row.get("ok", True):
                agg["exhausted"] += 1

    total_units = sum(a["units"] for a in by_label.values())
    total_repaired = sum(a["repaired"] for a in by_label.values())
    total_exhausted = sum(a["exhausted"] for a in by_label.values())

    print(f"файлов: {len(files)}; единиц работы: {total_units}\n")
    print(f"{'контракт':<22} {'единиц':>7} {'с repair':>9} {'rate':>7} {'исчерпано':>10}")
    for key in sorted(by_label):
        a = by_label[key]
        rate = a["repaired"] / a["units"] * 100 if a["units"] else 0.0
        print(f"{key:<22} {a['units']:>7} {a['repaired']:>9} {rate:>6.1f}% {a['exhausted']:>10}")
    rate = total_repaired / total_units * 100 if total_units else 0.0
    print(f"\nИТОГО repair-rate: {rate:.1f}% (порог приёмки ≤10%); исчерпаний: {total_exhausted} (нужно 0)")
    ok = rate <= 10.0 and total_exhausted == 0
    print("ПРИЁМКА:", "OK" if ok else "FAIL")
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
