#!/usr/bin/env python3
"""Grep провайдеров по сборке web/out и по ответам API (ТЗ п.22, п.49).

Запуск:
    python scripts/merge_checks/check_providers.py --out web/out
    python scripts/merge_checks/check_providers.py --out web/out --api http://127.0.0.1:8765
"""
from __future__ import annotations

import argparse
import pathlib
import re
import sys
import urllib.request

PROVIDER_RE = re.compile(
    r"(?i)\b(52pay|kie|outsee|vibecode|wavespeed|elevenlabs|anthropic|openai)\b"
)

TEXT_EXT = {".html", ".js", ".mjs", ".css", ".json", ".txt", ".svg", ".map", ".webmanifest"}

# Публичные эндпоинты для скана ответов (без авторизации)
API_PATHS = [
    "/api/billing/plans",
    "/api/models/health",
    "/openapi.json",
    "/api/definitely/not/here",   # тело 404 тоже не должно светить провайдеров
]


def scan_out(out_dir: pathlib.Path) -> list[str]:
    hits: list[str] = []
    for f in out_dir.rglob("*"):
        if not f.is_file() or f.suffix.lower() not in TEXT_EXT:
            continue
        text = f.read_text(encoding="utf-8", errors="replace")
        for i, line in enumerate(text.splitlines(), 1):
            m = PROVIDER_RE.search(line)
            if m:
                hits.append(f"{f}:{i}: «{m.group(0)}»")
    return hits


def scan_api(base: str) -> list[str]:
    hits: list[str] = []
    for path in API_PATHS:
        try:
            with urllib.request.urlopen(base.rstrip("/") + path, timeout=10) as resp:
                body = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:  # тело ошибки тоже проверяем
            body = e.read().decode("utf-8", errors="replace")
        except Exception as e:
            hits.append(f"{path}: недоступен ({e})")
            continue
        m = PROVIDER_RE.search(body)
        if m:
            hits.append(f"API {path}: «{m.group(0)}»")
    return hits


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="web/out")
    ap.add_argument("--api", default=None, help="базовый URL бэкенда, например http://127.0.0.1:8765")
    args = ap.parse_args()

    hits: list[str] = []
    out_dir = pathlib.Path(args.out)
    if out_dir.exists():
        hits += scan_out(out_dir)
    else:
        print(f"[warn] {out_dir} не найден — сборка фронта пропущена?")
    if args.api:
        hits += scan_api(args.api)

    if hits:
        print("=== УТЕЧКИ ПРОВАЙДЕРОВ (блокер, ТЗ п.22) ===")
        for h in hits:
            print(" ", h)
        return 1
    print("OK: имена провайдеров не найдены.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
