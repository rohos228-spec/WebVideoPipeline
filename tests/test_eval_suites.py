"""Наборы эвалов — тоже код, и ломаются они молча.

Сами эвалы в гейт не поставишь: они ходят в сеть и стоят денег. Но их
КОНФИГУРАЦИЯ проверяется бесплатно, и проверять её надо, потому что все
поломки здесь тихие: опечатка в `kind` даёт критерий, который никогда не
считается; ссылка на несуществующий контракт всплывает через месяц при ночном
прогоне; обвязка промта, разъехавшаяся с продакшеном, превращает эвал в
измерение промта, которого в природе нет.

Ни одна из этих поломок не делает прогон красным — она делает его
бессмысленным, а это хуже.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SUITES = ROOT / "evals" / "suites"

SUITE_FILES = sorted(SUITES.glob("*.json")) if SUITES.is_dir() else []


def _ident(path: Path) -> str:
    return path.stem


def test_there_is_at_least_one_suite() -> None:
    assert SUITE_FILES, f"в {SUITES} нет ни одного набора — эвалы существуют только на бумаге"


@pytest.mark.parametrize("path", SUITE_FILES, ids=_ident)
def test_suite_is_well_formed(path: Path) -> None:
    suite = json.loads(path.read_text(encoding="utf-8"))
    assert suite.get("name"), f"{path.name}: у набора нет имени — по нему ведётся храповик оценки"
    cases = suite.get("cases")
    assert isinstance(cases, list) and cases, f"{path.name}: набор без случаев"

    threshold = suite.get("threshold", 0.0)
    assert 0.0 <= float(threshold) <= 1.0, f"{path.name}: порог {threshold} вне [0, 1]"

    seen: set[str] = set()
    for case in cases:
        cid = case.get("id")
        assert cid, f"{path.name}: случай без id"
        assert cid not in seen, f"{path.name}: два случая с id {cid!r} — храповик их не различит"
        seen.add(cid)


@pytest.mark.parametrize("path", SUITE_FILES, ids=_ident)
def test_every_criterion_kind_is_known(path: Path) -> None:
    """Опечатка в `kind` — критерий, который молча не считается.

    Оценка при этом даже вырастет: несосчитанный критерий не попадает ни в
    пройденные, ни в проваленные, а делится оценка на их сумму.
    """
    from scripts.eval_prompts import CHEAP

    known = set(CHEAP) | {"judge"}
    suite = json.loads(path.read_text(encoding="utf-8"))
    for case in suite["cases"]:
        criteria = case.get("criteria") or []
        assert criteria, f"{path.name}/{case['id']}: случай без критериев ничего не меряет"
        for spec in criteria:
            kind = spec.get("kind")
            assert kind in known, (
                f"{path.name}/{case['id']}: критерий {kind!r} неизвестен; есть: {sorted(known)}"
            )
            if kind == "judge":
                assert spec.get("ask"), f"{path.name}/{case['id']}: judge без вопроса"
            if kind == "contract":
                from app.contracts import get_contract

                get_contract(spec["contract"])
            if kind in ("min_items", "max_chars", "contains", "absent"):
                assert "value" in spec or "path" in spec, (
                    f"{path.name}/{case['id']}: критерий {kind} без value"
                )


@pytest.mark.parametrize("path", SUITE_FILES, ids=_ident)
def test_prompt_source_resolves(path: Path) -> None:
    """У каждого случая есть, что послать модели.

    Промт-библиотека намеренно вне git, поэтому отсутствие ФАЙЛА — не ошибка
    конфигурации, а отсутствие данных: пропускаем. А вот случай, у которого не
    указано ни `prompt`, ни `prompt_file`, ни шага, — ошибка всегда.
    """
    from app.services import prompt_library

    suite = json.loads(path.read_text(encoding="utf-8"))
    for case in suite["cases"]:
        if case.get("prompt") or case.get("prompt_file"):
            continue
        step = case.get("step_code") or suite.get("step_code")
        assert step, f"{path.name}/{case['id']}: нечего слать — ни prompt, ни prompt_file, ни step_code"
        prompt_path = prompt_library.prompt_path(step, case.get("prompt_name") or "default")
        if not prompt_path.is_file():
            pytest.skip(f"{step}: промта нет на диске — библиотека вне git")


def test_plan_suffix_mirrors_production_footer() -> None:
    """Обвязка промта в наборе обязана совпадать с той, что уходит в прогоне.

    Мастер-промт — не весь промт: код дописывает вокруг него тему спереди и
    требуемый формат вывода сзади. Копия этой обвязки лежит в наборе, и копия
    имеет обыкновение расходиться. Разойдясь, она превращает эвал в измерение
    промта, которого не существует: критерии про формат вывода начинают
    проваливаться (или проходить) по причине, не связанной с качеством промта.
    """
    from app.services.chatgpt_xlsx import PLAN_XLSX_OUTPUT_FOOTER

    suite = json.loads((SUITES / "plan.json").read_text(encoding="utf-8"))
    assert suite["suffix"] == PLAN_XLSX_OUTPUT_FOOTER, (
        "suffix набора plan.json разошёлся с chatgpt_xlsx.PLAN_XLSX_OUTPUT_FOOTER. "
        "Правьте оба места или переносите обвязку в общий модуль."
    )


def test_baseline_names_match_existing_suites() -> None:
    """Храповик оценки ведётся по ИМЕНИ набора, а имена переименовывают.

    Переименовали набор — его строка в базе осиротела, а новый набор попал в
    базу с чистого листа и просесть уже не может. Так храповик и проворачивают
    назад, ничего не заметив.
    """
    baseline_path = ROOT / "evals" / "baseline.json"
    if not baseline_path.is_file():
        pytest.skip("базы оценок ещё нет — эвалы не гонялись")
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    names = {json.loads(p.read_text(encoding="utf-8"))["name"] for p in SUITE_FILES}
    orphans = sorted(k for k in baseline if not k.startswith("_") and k not in names)
    assert not orphans, f"в базе оценок есть наборы, которых больше нет: {', '.join(orphans)}"
