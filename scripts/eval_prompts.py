#!/usr/bin/env python3
"""Эвалы промтов: живой вызов модели, оценка ответа, храповик по оценке.

Отличие от голденсетов (`tests/test_prompt_goldens.py`) принципиальное и его
стоит держать в голове. Голден меряет НАШ код: тот же текст обязан разобраться
в те же данные, сети нет, ответ всегда один. Эвал меряет МОДЕЛЬ: годится ли
ответ по существу. Он ходит в сеть, стоит денег и повторяется по-разному.

Отсюда три следствия, заложенные в устройство:

1. **Эвал не стоит в push-гейте.** Гейт, который стоит рубль за нажатие,
   обходят на второй день. Место эвала — ночной прогон и правка промта:
   поменял промт, прогнал его набор до и после, сравнил.
2. **Порог, а не равенство.** Модель отвечает каждый раз иначе, и требовать
   совпадения значит получить красный прогон на исправном промте. Оценка —
   доля пройденных критериев; храповик ловит ПРОСАДКУ больше допуска.
3. **Критерии считаются по возрастанию цены.** Сперва бесплатные и
   детерминированные (контракт, счётчики, запреты), и лишь если они прошли —
   судья-модель. Спрашивать судью про ответ, не прошедший схему, незачем:
   ответ уже негоден, а вызов уже оплачен.

Использование::

    python3 scripts/eval_prompts.py --suite evals/suites/plan.json
    python3 scripts/eval_prompts.py --all --out evals/reports/2026-08-25.json
    python3 scripts/eval_prompts.py --all --dry-run     # что бы гонялось, без сети
    python3 scripts/eval_prompts.py --all --ratchet     # уронить при просадке

Коды возврата: 0 — порог взят (и просадки нет); 1 — не взят; 2 — ошибка
использования или конфигурации.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SUITES = ROOT / "evals" / "suites"
BASELINE = ROOT / "evals" / "baseline.json"

#: Насколько оценке позволено просесть, прежде чем это регрессия. Модели
#: плавают в пределах нескольких процентов между прогонами одного и того же
#: промта; допуск ниже этого шума превращает храповик в генератор ложных
#: тревог, а его отключают вместе с настоящими.
DEFAULT_TOLERANCE = 0.05


@dataclass
class CaseResult:
    case_id: str
    passed: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    reply: str = ""
    error: str = ""

    @property
    def score(self) -> float:
        total = len(self.passed) + len(self.failed)
        return len(self.passed) / total if total else 0.0


@dataclass
class SuiteResult:
    name: str
    cases: list[CaseResult] = field(default_factory=list)

    @property
    def score(self) -> float:
        return sum(c.score for c in self.cases) / len(self.cases) if self.cases else 0.0


# ── критерии ────────────────────────────────────────────────────────────────
#
# Каждый возвращает `None` (прошёл) или строку с причиной. Строка уезжает в
# отчёт и должна читаться человеком: «не прошёл критерий min_items» не
# объясняет ничего, «кадров 12, ожидалось не меньше 20» объясняет всё.


def _check_contract(reply: str, spec: dict) -> str | None:
    from app.contracts import LlmContractError, get_contract

    name = spec.get("contract") or ""
    try:
        get_contract(name).parse(reply)
    except LlmContractError as exc:
        return f"контракт {name} не пройден: {str(exc)[:300]}"
    except KeyError as exc:
        return f"контракт {name} неизвестен: {exc}"
    return None


def _dig(payload: Any, path: str) -> Any:
    for part in (path or "").split("."):
        if not part:
            continue
        if isinstance(payload, dict):
            payload = payload.get(part)
        else:
            return None
    return payload


def _check_min_items(reply: str, spec: dict) -> str | None:
    from app.contracts.extract import extract_json_payload

    try:
        data = extract_json_payload(reply, contract="eval")
    except Exception as exc:  # noqa: BLE001
        return f"не удалось прочитать JSON для подсчёта: {str(exc)[:200]}"
    got = _dig(data, spec.get("path", ""))
    if not isinstance(got, list):
        return f"по пути {spec.get('path')!r} не список, а {type(got).__name__}"
    need = int(spec.get("value", 1))
    if len(got) < need:
        return f"элементов {len(got)}, ожидалось не меньше {need}"
    return None


#: Слова, которыми модель затыкает дыру вместо ответа. Ответ с ними проходит
#: любую схему и при этом бесполезен — а заметить это по схеме нельзя.
_PLACEHOLDERS = (
    "lorem ipsum",
    "todo",
    "tbd",
    "заполнить",
    "здесь будет",
    "описание отсутствует",
    "n/a",
    "placeholder",
)


def _check_no_placeholder(reply: str, spec: dict) -> str | None:
    low = (reply or "").lower()
    hits = [w for w in _PLACEHOLDERS if w in low]
    if hits:
        return f"ответ содержит заглушки: {', '.join(hits)}"
    return None


def _check_contains(reply: str, spec: dict) -> str | None:
    needle = str(spec.get("value", ""))
    if needle and needle.lower() not in (reply or "").lower():
        return f"в ответе нет {needle!r}"
    return None


def _check_absent(reply: str, spec: dict) -> str | None:
    needle = str(spec.get("value", ""))
    if needle and needle.lower() in (reply or "").lower():
        return f"в ответе есть запрещённое {needle!r}"
    return None


def _check_max_chars(reply: str, spec: dict) -> str | None:
    limit = int(spec.get("value", 0))
    if limit and len(reply or "") > limit:
        return f"ответ {len(reply)} символов при пределе {limit}"
    return None


#: Дешёвые проверки: без сети, детерминированные. Гоняются первыми.
CHEAP = {
    "contract": _check_contract,
    "min_items": _check_min_items,
    "no_placeholder": _check_no_placeholder,
    "contains": _check_contains,
    "absent": _check_absent,
    "max_chars": _check_max_chars,
}

JUDGE_PROMPT = """Ты проверяешь ответ другой модели. Отвечай СТРОГО одним словом:
ДА — если утверждение о проверяемом ответе верно, НЕТ — если неверно.
Никаких пояснений.

Утверждение: {ask}

Проверяемый ответ:
---
{reply}
---
"""


async def _check_judge(reply: str, spec: dict) -> str | None:
    """Судья-модель. Дорогой критерий, потому и последний в очереди."""
    from app.services.gpt_api import chat

    ask = str(spec.get("ask") or "").strip()
    if not ask:
        return "критерий judge без вопроса"
    # Обрезаем: судить по первым тысячам символов дешевле и почти всегда
    # достаточно, а полный скелет на 85 КБ стоил бы как сам шаг.
    body = (reply or "")[: int(spec.get("max_chars", 12_000))]
    result = await chat(prompt=JUDGE_PROMPT.format(ask=ask, reply=body))
    verdict = (getattr(result, "text", "") or "").strip().lower()
    if verdict.startswith(("да", "yes")):
        return None
    return f"судья ответил «{verdict[:60]}» на: {ask}"


# ── прогон ──────────────────────────────────────────────────────────────────


def _render(template: str, variables: dict[str, Any]) -> str:
    out = template
    for key, value in (variables or {}).items():
        out = out.replace("{" + str(key) + "}", str(value))
    return out


def _load_prompt(suite: dict, case: dict) -> str:
    """Промт случая: явный текст, либо файл, либо шаг библиотеки."""
    if case.get("prompt"):
        return _render(str(case["prompt"]), case.get("vars", {}))
    if case.get("prompt_file"):
        path = (ROOT / str(case["prompt_file"])).resolve()
        return _render(path.read_text(encoding="utf-8"), case.get("vars", {}))

    step = case.get("step_code") or suite.get("step_code") or ""
    if not step:
        raise ValueError(f"случай {case.get('id')!r}: нечего слать — ни prompt, ни step_code")

    name = case.get("prompt_name") or "default"

    # Сперва переопределение из базы (правка оператора), потом файл на диске.
    # Порядок именно такой: эвал обязан мерить ТОТ промт, который поедет в
    # прогон, а не тот, что лежал в репозитории до правки. `prompt_store`
    # отвечает только когда библиотека поднята в память — вне приложения это
    # обычно не так, и тогда работает диск.
    from app.services import prompt_library, prompt_store

    text = prompt_store.resolve(step, name) if prompt_store.loaded() else None
    if not text:
        path = prompt_library.prompt_path(step, name)
        if not path.is_file():
            raise ValueError(
                f"шаг {step!r}: промта нет ни в базе, ни на диске ({path}). "
                f"Библиотека намеренно вне git — проверить: "
                f"python3 scripts/check_prompts.py"
            )
        text = path.read_text(encoding="utf-8")

    # Мастер-промт — не весь промт. Код дописывает вокруг него обвязку: тему
    # спереди, требуемый формат вывода сзади (см.
    # `chatgpt_xlsx.PLAN_XLSX_OUTPUT_FOOTER`). Эвал обязан слать модели ровно
    # то, что уходит в прогоне: без обвязки он мерил бы промт, которого в
    # природе нет, и «формат вывода» проваливался бы всегда.
    prefix = _render(str(case.get("prefix") or suite.get("prefix") or ""), case.get("vars", {}))
    suffix = _render(str(case.get("suffix") or suite.get("suffix") or ""), case.get("vars", {}))
    return prefix + _render(text, case.get("vars", {})) + suffix


async def _run_case(suite: dict, case: dict, *, dry_run: bool) -> CaseResult:
    result = CaseResult(case_id=str(case.get("id") or "без-имени"))
    criteria = list(case.get("criteria") or [])

    try:
        prompt = _load_prompt(suite, case)
    except Exception as exc:  # noqa: BLE001
        result.error = str(exc)
        result.failed = [f"промт не собрался: {exc}"]
        return result

    if dry_run:
        cheap = [c for c in criteria if c.get("kind") in CHEAP]
        judges = [c for c in criteria if c.get("kind") == "judge"]
        print(
            f"    [{result.case_id}] промт {len(prompt)} символов, "
            f"критериев: дешёвых {len(cheap)}, судейских {len(judges)}"
        )
        return result

    from app.services.gpt_api import chat

    try:
        reply = (getattr(await chat(prompt=prompt), "text", "") or "").strip()
    except Exception as exc:  # noqa: BLE001
        result.error = str(exc)
        result.failed = [f"вызов модели не удался: {str(exc)[:300]}"]
        return result

    result.reply = reply

    # Сперва дешёвые. Если хоть один упал — судью не зовём: ответ уже негоден,
    # а судья стоит денег.
    cheap_failed = False
    for spec in criteria:
        kind = spec.get("kind")
        fn = CHEAP.get(str(kind))
        if fn is None:
            continue
        problem = fn(reply, spec)
        if problem:
            result.failed.append(problem)
            cheap_failed = True
        else:
            result.passed.append(str(kind))

    for spec in criteria:
        if spec.get("kind") != "judge":
            continue
        if cheap_failed:
            result.failed.append(f"судья не спрошен: ответ не прошёл дешёвые проверки ({spec.get('ask')})")
            continue
        problem = await _check_judge(reply, spec)
        if problem:
            result.failed.append(problem)
        else:
            result.passed.append("judge")

    return result


async def _run_suite(path: Path, *, dry_run: bool) -> SuiteResult:
    suite = json.loads(path.read_text(encoding="utf-8"))
    out = SuiteResult(name=str(suite.get("name") or path.stem))
    print(f"  набор {out.name} ({len(suite.get('cases') or [])} случаев)")
    for case in suite.get("cases") or []:
        out.cases.append(await _run_case(suite, case, dry_run=dry_run))
    return out


def _load_baseline() -> dict[str, float]:
    if not BASELINE.is_file():
        return {}
    raw = json.loads(BASELINE.read_text(encoding="utf-8"))
    return {k: float(v) for k, v in raw.items() if not k.startswith("_")}


def _save_baseline(scores: dict[str, float]) -> None:
    BASELINE.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "_comment": (
            "Храповик оценки эвалов (scripts/eval_prompts.py --ratchet). "
            "Просадка больше допуска — красный; рост подтягивается сам, "
            "и сужение должно быть видно в диффе."
        ),
        **{k: round(v, 4) for k, v in sorted(scores.items())},
    }
    BASELINE.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")


async def _amain(args: argparse.Namespace) -> int:
    paths: list[Path]
    if args.all:
        paths = sorted(SUITES.glob("*.json")) if SUITES.is_dir() else []
    elif args.suite:
        paths = [Path(args.suite)]
    else:
        print("нужен --suite <файл> или --all", file=sys.stderr)
        return 2

    if not paths:
        print(f"наборов нет: {SUITES} пуст", file=sys.stderr)
        return 2

    print(f"эвалы: наборов {len(paths)}{' (без сети)' if args.dry_run else ''}")
    results = [await _run_suite(p, dry_run=args.dry_run) for p in paths]

    if args.dry_run:
        print("\nсухой прогон: вызовов не было, оценок нет")
        return 0

    print()
    ok = True
    scores: dict[str, float] = {}
    for r in results:
        scores[r.name] = r.score
        print(f"{r.name}: оценка {r.score:.2f}")
        for c in r.cases:
            mark = "✓" if not c.failed else "✗"
            print(f"  {mark} {c.case_id}: {c.score:.2f}")
            for problem in c.failed:
                print(f"      — {problem}")

    # Порог берётся из самого набора: у разных шагов разная планка.
    for path, r in zip(paths, results, strict=True):
        threshold = float(json.loads(path.read_text(encoding="utf-8")).get("threshold", 0.0))
        if threshold and r.score + 1e-9 < threshold:
            print(f"\n{r.name}: {r.score:.2f} ниже порога {threshold:.2f}")
            ok = False

    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(
                {
                    r.name: {
                        "score": round(r.score, 4),
                        "cases": [
                            {"id": c.case_id, "score": round(c.score, 4), "failed": c.failed} for c in r.cases
                        ],
                    }
                    for r in results
                },
                ensure_ascii=False,
                indent=1,
            ),
            encoding="utf-8",
        )
        print(f"\nотчёт: {out}")

    if args.ratchet:
        base = _load_baseline()
        regressed = [
            f"{name}: {score:.2f} против {base[name]:.2f} в базе"
            for name, score in scores.items()
            if name in base and score + args.tolerance < base[name]
        ]
        if regressed:
            print("\nпросадка оценки:")
            for line in regressed:
                print(f"  {line}")
            ok = False
        else:
            grown = {n: s for n, s in scores.items() if s > base.get(n, -1.0)}
            if grown:
                _save_baseline({**base, **scores})
                print(f"\nбаза подтянута ({', '.join(sorted(grown))}) — закоммитить evals/baseline.json")

    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Эвалы промтов")
    parser.add_argument("--suite", default="", help="один набор")
    parser.add_argument("--all", action="store_true", help="все наборы из evals/suites")
    parser.add_argument("--out", default="", help="куда записать отчёт")
    parser.add_argument("--dry-run", action="store_true", help="без сети: что бы гонялось")
    parser.add_argument("--ratchet", action="store_true", help="уронить при просадке оценки")
    parser.add_argument("--tolerance", type=float, default=DEFAULT_TOLERANCE)
    args = parser.parse_args(argv)
    return asyncio.run(_amain(args))


if __name__ == "__main__":
    raise SystemExit(main())
