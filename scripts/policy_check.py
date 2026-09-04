#!/usr/bin/env python3
"""Машинный исполнитель инженерной политики (`docs/ENGINEERING-POLICY.md`).

Гейт ловит качество кода; этот шаг ловит нарушения ПРОЦЕССА, которые
иначе замечает только человек и только когда уже поздно:

* прямой коммит в `main` (§2);
* рост долга — исключения в diff-cov, hex-гейте, gitleaks (§12, WR-5);
* контрактное изменение без спеки openspec (§3, WR-2);
* заброшенный change: закрытые задачи, но не в архиве (§3);
* просроченный долг — строка в реестре старше 90 дней (§12).

Долг сверяется **по составу, а не по числу**: одно узкое исключение,
заменённое исключением каталога, оставило бы счётчик прежним и расширило
дыру. Любая строка, которой нет в базе, — красный.

Запуск:

    python3 scripts/policy_check.py              # против origin/main
    python3 scripts/policy_check.py --base HEAD~1
    python3 scripts/policy_check.py --update-debt   # после ПОГАШЕНИЯ долга

Коды возврата: 0 — чисто, 1 — нарушение, 2 — ошибка запуска.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import UTC, date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEBT_BASELINE = ROOT / ".claude" / "baselines" / "debt.json"
DEBT_REGISTRY = ROOT / "docs" / "DEBT.md"
VERIFY = ROOT / ".claude" / "verify.json"
CI = ROOT / ".github" / "workflows" / "ci.yml"
GITLEAKSIGNORE = ROOT / ".gitleaksignore"
CHANGES = ROOT / "docs" / "openspec" / "changes"

#: Пути, изменение которых считается контрактным или рискованным (§3, §5):
#: спека обязательна независимо от размера диффа.
CONTRACT_PATHS = (
    "app/web/routers/",
    "app/web/schemas.py",
    "app/web/api.py",
    "app/web/identity.py",
    "migrations/",
    "app/models.py",
    "app/orchestrator/node_registry.py",
    "app/orchestrator/default_graph.py",
    "app/services/prompt_library.py",
    "app/services/billing",
    "app/bots/",
    "templates/",
)
#: Срок жизни строки долга без решения владельца (§12).
DEBT_MAX_AGE_DAYS = 90


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def changed_files(base: str) -> list[str]:
    return [p for p in git("diff", "--name-only", f"{base}...HEAD").splitlines() if p]


def collect_debt() -> dict[str, list[str]]:
    """Состав исключений в каждом гейте. Список, а не счётчик: подмена
    узкого исключения широким иначе прошла бы незамеченной."""
    debt: dict[str, list[str]] = {}

    verify = json.loads(VERIFY.read_text(encoding="utf-8"))
    diff_cov_cmd = ""
    for repo in verify.get("repos", []):
        for check in repo.get("push", []):
            if check.get("name") == "diff-cov":
                diff_cov_cmd = check.get("run", "")
    debt["diff_cov_excludes"] = sorted(re.findall(r"--exclude\s+(\S+)", diff_cov_cmd))

    ci_text = CI.read_text(encoding="utf-8") if CI.is_file() else ""
    hex_block = ""
    if "Никакого hex вне токенов" in ci_text:
        start = ci_text.index("Никакого hex вне токенов")
        end = ci_text.find("- name:", start + 10)
        hex_block = ci_text[start : end if end > 0 else len(ci_text)]
    debt["hex_excludes"] = sorted(re.findall(r"-e\s+'([^']+)'", hex_block))

    leaks = GITLEAKSIGNORE.read_text(encoding="utf-8") if GITLEAKSIGNORE.is_file() else ""
    debt["gitleaks_fingerprints"] = sorted(
        ln.strip() for ln in leaks.splitlines() if ln.strip() and not ln.startswith("#")
    )
    return debt


def load_baseline() -> dict[str, list[str]]:
    if not DEBT_BASELINE.is_file():
        return {}
    raw = json.loads(DEBT_BASELINE.read_text(encoding="utf-8")).get("debt", {})
    return {k: sorted(v) for k, v in raw.items()}


def write_baseline(debt: dict[str, list[str]]) -> None:
    DEBT_BASELINE.parent.mkdir(parents=True, exist_ok=True)
    DEBT_BASELINE.write_text(
        json.dumps(
            {
                "_comment": (
                    "База долга (docs/ENGINEERING-POLICY.md §12). Сверяется СОСТАВ: "
                    "любая строка, которой здесь нет, — красный policy-check. "
                    "Обновлять после ПОГАШЕНИЯ: policy_check.py --update-debt."
                ),
                "updated": datetime.now(UTC).date().isoformat(),
                "debt": debt,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def active_changes() -> list[Path]:
    if not CHANGES.is_dir():
        return []
    return [p for p in CHANGES.iterdir() if p.is_dir() and p.name != "archive"]


def check_branch(problems: list[str]) -> None:
    try:
        branch = git("rev-parse", "--abbrev-ref", "HEAD")
        ahead = int(git("rev-list", "--count", "origin/main..HEAD"))
    except subprocess.CalledProcessError:
        return
    if branch == "main" and ahead:
        problems.append(
            f"§2: {ahead} коммит(ов) сделано прямо в main. Работа идёт в ветке "
            "feat|fix|chore|docs/<тема>, в main изменение попадает слиянием."
        )


def check_debt(problems: list[str], debt: dict[str, list[str]], base: dict[str, list[str]]) -> None:
    if not base:
        problems.append(
            "§12: базы долга нет. Сними её один раз: python3 scripts/policy_check.py --update-debt"
        )
        return
    for key, entries in debt.items():
        known = set(base.get(key, []))
        added = [e for e in entries if e not in known]
        if added:
            shown = ", ".join(added[:3]) + (" …" if len(added) > 3 else "")
            problems.append(
                f"§12/WR-5: новое исключение в {key}: {shown}. Исключение заводится "
                "только вместе со строкой в docs/DEBT.md и решением владельца; "
                "после погашения база обновляется --update-debt."
            )


def check_debt_age(problems: list[str]) -> None:
    """Строка реестра старше 90 дней требует решения (§12)."""
    if not DEBT_REGISTRY.is_file():
        problems.append("§12: нет реестра долга docs/DEBT.md")
        return
    today = date.today()
    for line in DEBT_REGISTRY.read_text(encoding="utf-8").splitlines():
        if not line.startswith("|") or "Погашено" in line:
            continue
        found = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", line)
        if not found:
            continue
        age = (today - date.fromisoformat(found.group(1))).days
        if age > DEBT_MAX_AGE_DAYS:
            cells = [c.strip() for c in line.split("|")]
            what = cells[2][:60] if len(cells) > 2 else line[:60]
            problems.append(
                f"§12: долгу «{what}» {age} дней (>{DEBT_MAX_AGE_DAYS}). Нужно решение "
                "владельца: гасить или признать постоянным исключением с обоснованием "
                "(тогда обнови дату и припиши «постоянное»)."
            )


def check_spec(problems: list[str], files: list[str]) -> None:
    """Спека обязательна по СМЫСЛУ изменения, а не по числу файлов:
    форматирование шести файлов спеки не требует, правка биллинга в двух —
    требует."""
    contract = [f for f in files if f.startswith(CONTRACT_PATHS)]
    if not contract or active_changes():
        return
    problems.append(
        f"§3/WR-2: изменение трогает контракт ({contract[0]}"
        + (f" и ещё {len(contract) - 1}" if len(contract) > 1 else "")
        + "), а активного change в docs/openspec/changes/ нет. Спека впереди кода: "
        "заведи proposal.md + tasks.md."
    )


#: Проверки, без которых гейт перестаёт быть гейтом. Список намеренно
#: дублирует verify.json: файл могли не ослабить, а выкинуть целиком, и
#: тогда сверять было бы нечего.
REQUIRED_CHECKS = {
    "commit": {"ruff", "mypy", "secrets", "tests-touched"},
    "push": {
        "pytest-ratchet",
        "cov-ratchet",
        "diff-cov",
        "ruff",
        "mypy",
        "secrets",
        "migrations",
        "rls",
        "api-surface",
        "policy",
    },
}


def check_gate_intact(problems: list[str]) -> None:
    """Гейт на месте и не выпотрошен (§ «Честная граница»)."""
    for path in (VERIFY, ROOT / ".githooks" / "pre-push", ROOT / ".githooks" / "pre-commit"):
        if not path.is_file():
            problems.append(f"гейт снят: нет {path.relative_to(ROOT)}")
            return
    verify = json.loads(VERIFY.read_text(encoding="utf-8"))
    have: dict[str, set[str]] = {stage: set() for stage in REQUIRED_CHECKS}
    for repo in verify.get("repos", []):
        for stage in REQUIRED_CHECKS:
            have[stage] |= {c.get("name", "") for c in repo.get(stage, [])}
    for stage, need in REQUIRED_CHECKS.items():
        missing = sorted(need - have[stage])
        if missing:
            problems.append(
                f"из яруса {stage} пропали проверки: {', '.join(missing)}. "
                "Ослабление гейта — решение владельца с записью в docs/DEBT.md."
            )


def check_change_hygiene(problems: list[str]) -> None:
    for change in active_changes():
        tasks = change / "tasks.md"
        if not tasks.is_file():
            continue
        text = tasks.read_text(encoding="utf-8")
        if text.count("- [x]") and not text.count("- [ ]"):
            problems.append(
                f"§3: change «{change.name}» закрыт целиком, но лежит среди активных. "
                "Переноси в docs/openspec/changes/archive/<дата>-<id>/ и обнови specs/."
            )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Проверка инженерной политики")
    parser.add_argument("--base", default="", help="база сравнения (по умолчанию origin/main)")
    parser.add_argument("--update-debt", action="store_true", help="переписать базу долга (после погашения)")
    args = parser.parse_args(argv)

    debt = collect_debt()
    if args.update_debt:
        write_baseline(debt)
        sizes = {k: len(v) for k, v in debt.items()}
        print(f"база долга записана: {sizes} → {DEBT_BASELINE.relative_to(ROOT)}")
        return 0

    base_ref = args.base or "origin/main"
    try:
        git("rev-parse", "--verify", base_ref)
    except subprocess.CalledProcessError:
        print(f"policy-check: нет ссылки {base_ref} — сверку диффа пропускаю", file=sys.stderr)
        base_ref = ""

    problems: list[str] = []
    check_gate_intact(problems)
    check_branch(problems)
    check_debt(problems, debt, load_baseline())
    check_debt_age(problems)
    if base_ref:
        check_spec(problems, changed_files(base_ref))
    check_change_hygiene(problems)

    if problems:
        print("политика нарушена (docs/ENGINEERING-POLICY.md):")
        for p in problems:
            print(f"  • {p}")
        return 1
    sizes = {k: len(v) for k, v in debt.items()}
    print(f"политика соблюдена (долг: {sizes})")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (subprocess.CalledProcessError, OSError, json.JSONDecodeError) as exc:
        print(f"policy-check: не смог проверить: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
