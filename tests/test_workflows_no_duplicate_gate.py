"""CI не дублируется с гейтом релиза.

`release.yml` вызывает `ci.yml` как reusable workflow — это и есть гейт, без
которого образ не собирается. Пока `ci.yml` дополнительно запускался по push
на `main`, на каждую выкладку поднималось два одинаковых набора работ.

Они не просто жгли минуты, а дрались за раннеров: 2026-08-26 релиз простоял в
`pending`, пока его же двойник занимал слоты, и выкладку пришлось разблокировать
руками. Плюс на `main` из-за `cancel-in-progress: false` в группе держится один
идущий и один ожидающий — серия push'ей отменяла ожидающих, и проверенным
оказывался первый коммит серии, а не последний.

Ошибка тихая в обе стороны: вернуть `main` в триггер — и всё «работает», просто
вдвое дольше и с гонкой. Убрать вызов из релиза — и main перестанет
проверяться вовсе. Поэтому проверяются оба конца связи.
"""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CI = ROOT / ".github" / "workflows" / "ci.yml"
RELEASE = ROOT / ".github" / "workflows" / "release.yml"


def _load(path: Path) -> dict:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    # В YAML голое `on:` разбирается как булев True — это известная ловушка
    # спецификации, а не опечатка в файле.
    if True in data:
        data["on"] = data.pop(True)
    return data


def test_release_uses_ci_as_its_gate() -> None:
    """Гейт релиза — это именно ci.yml, а не отдельная копия проверок."""
    jobs = _load(RELEASE)["jobs"]
    gate = jobs.get("gate")
    assert gate is not None, "в релизе нет работы `gate`"
    assert gate.get("uses", "").endswith("ci.yml"), (
        f"гейт релиза больше не вызывает ci.yml (uses={gate.get('uses')!r}) — "
        "тогда main остаётся без проверки, и push-триггер надо возвращать"
    )


def test_ci_is_callable() -> None:
    """Без `workflow_call` релиз не сможет его вызвать."""
    assert "workflow_call" in _load(CI)["on"]


def test_ci_does_not_also_run_on_main_push() -> None:
    """Иначе на каждую выкладку — два одинаковых прогона и гонка за раннеров."""
    push = _load(CI)["on"]["push"]
    branches = push.get("branches") or []
    ignored = push.get("branches-ignore") or []

    assert "main" in ignored or ("**" not in branches and "main" not in branches), (
        f"ci.yml запускается по push на main (branches={branches}, "
        f"branches-ignore={ignored}), хотя его же вызывает релиз"
    )


def test_other_branches_still_run_ci() -> None:
    """Ветки проверяются по push — pull_request снят 2026-09-14 против дублей."""
    on = _load(CI)["on"]
    push = on["push"]
    assert push.get("branches-ignore") == ["main"], (
        f"ожидалось, что исключён ровно main, а остальные ветки идут как раньше; сейчас: {push!r}"
    )
