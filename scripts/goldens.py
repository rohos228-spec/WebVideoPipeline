#!/usr/bin/env python3
"""Сопровождение корпуса голденсетов: добавить случай, обновить, посмотреть.

Корпус живёт в `evals/golden/<контракт>/<имя>.json`, гейт по нему —
`tests/test_prompt_goldens.py`. Сырьё приходит из записи живого прогона:

    LLM_RECORD_DIR=/tmp/vp-record python3 -m app.main      # прогнать конвейер
    python3 scripts/goldens.py list /tmp/vp-record         # что записалось
    python3 scripts/goldens.py add /tmp/vp-record/vp_sd_skeleton__*.json \\
        --name живой-разбор --note "модель вернула лишний ключ"

Ожидание считается ЗДЕСЬ, из самого контракта: случай, чьё ожидание набито
руками, проверяет аккуратность набиравшего. Если ответ контракт не проходит,
случай записывается как ожидаемый отказ — это тоже голден, и часто более
ценный: он фиксирует, ЧТО именно модель делает не так.

Обновление после осознанной правки контракта:

    python3 scripts/goldens.py bless evals/golden/vp_sd_skeleton/live-2026-08-23.json

`bless` перезаписывает ожидание текущим поведением и печатает дифф — потому
что молча обновлённый голден это голден, который перестал что-либо держать.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "evals" / "golden"


def _expectation(contract_name: str, reply: str) -> dict:
    """Ожидание считается контрактом, а не человеком."""
    from app.contracts import LlmContractError, get_contract

    contract = get_contract(contract_name)
    try:
        parsed = contract.parse(reply)
    except LlmContractError as exc:
        return {"ok": False, "kind": exc.kind, "error": str(exc)[:400]}
    return {"ok": True, "payload": json.loads(parsed.payload.model_dump_json(by_alias=True))}


def cmd_list(args: argparse.Namespace) -> int:
    src = Path(args.dir)
    if not src.is_dir():
        print(f"нет каталога {src}", file=sys.stderr)
        return 2
    files = sorted(src.glob("*.json"))
    if not files:
        print(f"{src}: пусто. Прогон шёл без LLM_RECORD_DIR?")
        return 0
    for f in files:
        try:
            rec = json.loads(f.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            print(f"{f.name}: не читается ({exc})")
            continue
        print(
            f"{f.name}\n"
            f"    контракт {rec.get('contract', '?')}  вердикт {rec.get('verdict', '?')}  "
            f"{len(rec.get('reply', ''))} символов  узел {rec.get('node_key') or '—'}"
        )
    return 0


def cmd_add(args: argparse.Namespace) -> int:
    src = Path(args.record)
    rec = json.loads(src.read_text(encoding="utf-8"))
    contract = args.contract or rec.get("contract")
    reply = rec.get("reply") or ""
    if not contract:
        print("в записи нет контракта — укажите --contract", file=sys.stderr)
        return 2
    if not reply.strip():
        print("в записи пустой ответ — добавлять нечего", file=sys.stderr)
        return 2
    if rec.get("truncated"):
        print(
            "ВНИМАНИЕ: ответ был обрезан рекордером. Голден из обрезанного текста "
            "проверяет обработку обрезка, а не ответа модели.",
            file=sys.stderr,
        )

    name = args.name or src.stem.split("__")[0]
    dest = GOLDEN / contract / f"{name}.json"
    if dest.exists() and not args.force:
        print(f"{dest} уже есть; --force чтобы перезаписать", file=sys.stderr)
        return 1

    expect = _expectation(contract, reply)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(
        json.dumps(
            {"contract": contract, "note": args.note, "expect": expect, "reply": reply},
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
    verdict = "проходит контракт" if expect["ok"] else f"ОТКАЗ ({expect['kind']})"
    print(f"добавлено: {dest.relative_to(ROOT)}  —  {verdict}")
    if not expect["ok"]:
        print(f"    {expect['error'][:200]}")
    return 0


def cmd_bless(args: argparse.Namespace) -> int:
    dest = Path(args.case)
    case = json.loads(dest.read_text(encoding="utf-8"))
    before = case.get("expect", {})
    after = _expectation(case["contract"], case["reply"])

    if before == after:
        print(f"{dest.name}: ожидание не изменилось, править нечего")
        return 0

    print(f"{dest.name}: ожидание меняется")
    print(f"  было:  ok={before.get('ok')} {before.get('kind', '')}")
    print(f"  стало: ok={after.get('ok')} {after.get('kind', '')}")
    if before.get("ok") and after.get("ok"):
        changed = _diff_keys(before.get("payload", {}), after.get("payload", {}))
        print(f"  расходятся ключи: {', '.join(changed[:20]) or '(порядок или значения вложенных)'}")
    if not args.yes:
        print("\nдобавьте --yes, если правка контракта осознанная")
        return 1

    case["expect"] = after
    dest.write_text(json.dumps(case, ensure_ascii=False, indent=1), encoding="utf-8")
    print("обновлено")
    return 0


def _diff_keys(a: dict, b: dict) -> list[str]:
    keys = sorted(set(a) | set(b))
    return [k for k in keys if a.get(k) != b.get(k)]


def cmd_show(args: argparse.Namespace) -> int:
    if not GOLDEN.is_dir():
        print("корпуса нет вовсе")
        return 0
    from app.contracts import _REGISTRY

    total = 0
    for d in sorted(GOLDEN.iterdir()):
        if not d.is_dir():
            continue
        cases = sorted(d.glob("*.json"))
        total += len(cases)
        ok = sum(1 for c in cases if json.loads(c.read_text(encoding="utf-8"))["expect"]["ok"])
        print(f"{d.name:20} случаев {len(cases):3}  из них проходят {ok:3}, отказов {len(cases) - ok}")
    covered = {d.name for d in GOLDEN.iterdir() if d.is_dir()}
    missing = sorted(set(_REGISTRY) - covered)
    print(f"\nвсего случаев: {total}")
    if missing:
        print(f"без единого случая: {', '.join(missing)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Корпус голденсетов")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("list", help="что записал рекордер")
    p.add_argument("dir", help="каталог из LLM_RECORD_DIR")
    p.set_defaults(fn=cmd_list)

    p = sub.add_parser("add", help="перенести запись в корпус")
    p.add_argument("record", help="файл записи")
    p.add_argument("--name", default="", help="имя случая в корпусе")
    p.add_argument("--contract", default="", help="если в записи не указан")
    p.add_argument("--note", default="", help="чем случай интересен")
    p.add_argument("--force", action="store_true")
    p.set_defaults(fn=cmd_add)

    p = sub.add_parser("bless", help="обновить ожидание под текущее поведение")
    p.add_argument("case", help="файл случая в evals/golden")
    p.add_argument("--yes", action="store_true")
    p.set_defaults(fn=cmd_bless)

    p = sub.add_parser("show", help="что в корпусе есть и чего нет")
    p.set_defaults(fn=cmd_show)

    args = parser.parse_args(argv)
    return int(args.fn(args))


if __name__ == "__main__":
    raise SystemExit(main())
