#!/usr/bin/env python3
"""Сверка параметров настроек нод между main и тестовой веткой (ТЗ п.8–12, 15).

Сравнивает множества идентификаторов параметров, извлечённых из исходников обеих
веток через `git show`. Любой параметр, который есть в main и пропал в тестовой
ветке, — потенциальная «тихая потеря» при слиянии => exit 1.

Запуск:
    python scripts/merge_checks/check_node_step_params.py \
        --base upstream/main --head test/merge-housepc
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys

# Файлы с определениями параметров нод (правим список при переименованиях)
FILES = [
    "web/components/studio/node-step-params-panel.tsx",
    "web/components/studio/excel-gpt-settings-panel.tsx",
    "web/components/studio/node-studio.tsx",
    "web/components/canvas/node-model-picker.tsx",
    "app/services/prompt_style_vars.py",
]

# Паттерны идентификаторов параметров в коде
PARAM_PATTERNS = [
    re.compile(r"""params\.([A-Za-z_][A-Za-z0-9_]*)"""),          # params.foo
    re.compile(r"""['"]([a-z][a-z0-9_]{2,})['"]\s*:"""),          # 'foo': ...
    re.compile(r"""name=['"]([A-Za-z_][A-Za-z0-9_]*)['"]"""),     # name="foo"
    re.compile(r"""setParam\(\s*['"]([A-Za-z0-9_]+)['"]"""),      # setParam('foo'
    re.compile(r"""node_step_params\[['"]([A-Za-z0-9_]+)['"]\]"""),
]

# Служебные слова, не являющиеся параметрами
NOISE = {
    "true", "false", "null", "none", "type", "key", "value", "label",
    "class", "style", "children", "default", "import", "export",
}


def git_show(ref: str, path: str) -> str | None:
    p = subprocess.run(
        ["git", "show", f"{ref}:{path}"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return p.stdout if p.returncode == 0 else None


def extract_params(src: str) -> set[str]:
    found: set[str] = set()
    for pat in PARAM_PATTERNS:
        for m in pat.finditer(src):
            name = m.group(1)
            if name.lower() not in NOISE and len(name) >= 3:
                found.add(name)
    return found


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="upstream/main")
    ap.add_argument("--head", default="test/merge-housepc")
    args = ap.parse_args()

    lost_total: dict[str, set[str]] = {}
    added_total: dict[str, set[str]] = {}

    for path in FILES:
        base_src = git_show(args.base, path)
        head_src = git_show(args.head, path)
        if base_src is None:
            print(f"[skip] {path}: нет в {args.base}")
            continue
        if head_src is None:
            print(f"[FAIL] {path}: файл есть в {args.base}, но отсутствует в {args.head}")
            lost_total[path] = extract_params(base_src)
            continue
        base_params = extract_params(base_src)
        head_params = extract_params(head_src)
        lost = base_params - head_params
        added = head_params - base_params
        if lost:
            lost_total[path] = lost
        if added:
            added_total[path] = added

    print("\n=== Параметры, ДОБАВЛЕННЫЕ в тестовой ветке (информационно) ===")
    for path, names in sorted(added_total.items()):
        print(f"  {path}: {', '.join(sorted(names))}")

    if lost_total:
        print("\n=== ПОТЕРЯННЫЕ параметры main (блокер, ТЗ п.8–12/15) ===")
        for path, names in sorted(lost_total.items()):
            print(f"  {path}: {', '.join(sorted(names))}")
        return 1

    print("\nOK: все параметры main сохранены в тестовой ветке.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
