"""CI не дублируется: один прогон на коммит, и main без проверки не остаётся.

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

Второй виток той же истории (2026-09-15). Сперва из `ci.yml` убрали
`pull_request` — дубль исчез, но вместе с ним пропала проверка PR, и этот
файл покраснел, а следом весь гейт релиза. Потом триггеры вернули и свели
группу `concurrency` по ИМЕНИ ветки: лишний прогон стал отменяться, но в
списке проверок оставался хвостом «отменён», а быстрые работы успевали
завершиться дважды. Итог: `ci.yml` идёт только по `pull_request`, `policy.yml`
— только по `push`. Один прогон на коммит у каждого, дублировать нечем.
"""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CI = ROOT / ".github" / "workflows" / "ci.yml"
RELEASE = ROOT / ".github" / "workflows" / "release.yml"
POLICY = ROOT / ".github" / "workflows" / "policy.yml"


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


def test_ci_checks_pull_requests() -> None:
    """Проверка PR — единственный путь кода в main, без неё гейта нет вовсе."""
    assert "pull_request" in _load(CI)["on"], (
        "ci.yml перестал проверять pull request: ветка попадёт в main непроверенной, "
        "а гейт релиза окажется первой и последней проверкой"
    )


def test_ci_runs_once_per_commit() -> None:
    """Ровно один прогон на коммит: push-триггера у ci.yml быть не должно.

    С ним на каждый коммит в ветке с открытым PR встаёт вторая работа. Даже
    когда её отменяет `concurrency`, в списке проверок остаётся хвост
    «отменён», а работы короче минуты успевают завершиться оба раза.
    """
    on = _load(CI)["on"]
    assert "push" not in on, (
        f"push-триггер вернулся в ci.yml ({on.get('push')!r}) — снова пара прогонов "
        "на коммит; ветки проверяются через свой PR, main — вызовом из релиза"
    )


def test_policy_runs_on_every_push_including_main() -> None:
    """Политика — второй забор: она и ловит push в main, меняющий только гейт.

    `ci.yml` на main по push не идёт, а `release.yml` игнорирует `.claude/**`.
    Пересечение этих правил однажды дало дыру: правка `.claude/verify.json`,
    то есть ослабление самого гейта, не запускала ничего.
    """
    on = _load(POLICY)["on"]
    assert "push" in on, "без push-триггера политика перестанет видеть main"
    push = on.get("push") or {}
    assert not push.get("branches") and not push.get("branches-ignore"), (
        f"push политики сузили до части веток ({push!r}) — дыра возвращается"
    )


def test_policy_does_not_double_run() -> None:
    """У политики push уже покрывает и ветки, и main — PR-триггер дал бы дубль."""
    assert "pull_request" not in _load(POLICY)["on"], (
        "в policy.yml вернулся pull_request: на каждый коммит в PR будет две одинаковые работы"
    )
