"""Голденсеты: реальные ответы моделей прогоняются через контракты без сети.

**Что это проверяет и чего не проверяет.** Не качество модели — его меряют
эвалы (`scripts/eval_prompts.py`), они ходят в сеть, стоят денег и в гейт не
годятся. Здесь проверяется НАШ код: тот же текст, который модель вернула на
живом прогоне, обязан разобраться в те же данные. Изменили контракт, тронули
извлечение JSON, переименовали поле — тест краснеет, и краснеет на настоящем
ответе, а не на придуманном примере.

**Почему на настоящем.** Написанный из головы «пример ответа» проверяет наши
представления о модели. Реальность выглядит иначе, и корпус это фиксирует:
случаи `vp_apply_ops/*` — живой отказ прогона 2026-08-23, где модель вернула
`phase_index`, `beat` и `переход` вместо канонических имён. Такой ответ не
придумаешь; его можно только записать.

**Откуда берутся случаи.** Из `LLM_RECORD_DIR` (см.
`app/services/llm_recorder.py`): прогон с этой переменной кладёт каждую пару
«запрос → ответ» на диск, оттуда случай переносится сюда командой
`python3 scripts/goldens.py add <файл>`. Стартовый корпус собран из артефактов
живого прогона 2026-08-23 — `scene_design/*.json` и `llm_rejects/*.txt`.

**Формат случая** (`evals/golden/<контракт>/<имя>.json`):

    {
      "contract": "vp_sd_skeleton",
      "note": "чем этот случай интересен",
      "expect": {"ok": true, "payload": {...}},   // или {"ok": false, "kind": "validate"}
      "reply": "сырой текст, который вернула модель"
    }

`payload` — канонический вид ПОСЛЕ разбора. Сверяется он целиком, а не по
паре ключей: голден, проверяющий три поля из сорока, зелен на любой
перестановке остальных тридцати семи.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.contracts import LlmContractError, get_contract

GOLDEN_DIR = Path(__file__).resolve().parents[1] / "evals" / "golden"


def _cases() -> list[Path]:
    if not GOLDEN_DIR.is_dir():
        return []
    return sorted(GOLDEN_DIR.glob("*/*.json"))


def _ident(path: Path) -> str:
    return f"{path.parent.name}/{path.stem}"


CASES = _cases()


def test_the_corpus_is_not_empty() -> None:
    """Пустой корпус — зелёный файл, который ничего не проверяет.

    Ровно так голденсеты и умирают: случаи не переносят из записи, каталог
    остаётся пустым, параметризация даёт ноль тестов, и прогон радостно
    сообщает «всё хорошо».
    """
    assert CASES, (
        f"в {GOLDEN_DIR} нет ни одного случая. Собрать: прогнать конвейер с "
        f"LLM_RECORD_DIR=<каталог>, затем python3 scripts/goldens.py add <файл>"
    )


@pytest.mark.parametrize("path", CASES, ids=_ident)
def test_case_file_is_well_formed(path: Path) -> None:
    """Сам случай — тоже данные, и битый случай обязан падать явно.

    Иначе опечатка в `expect` даёт тест, который проверяет не то, что написано
    на этикетке, и заметить это можно только чтением.
    """
    case = json.loads(path.read_text(encoding="utf-8"))
    assert case.get("contract"), f"{path}: не указан контракт"
    assert isinstance(case.get("reply"), str) and case["reply"].strip(), f"{path}: пустой ответ"
    expect = case.get("expect")
    assert isinstance(expect, dict) and "ok" in expect, f"{path}: нет expect.ok"
    if expect["ok"]:
        assert "payload" in expect, f"{path}: успешный случай без ожидаемого payload"
    else:
        assert expect.get("kind") in ("parse", "validate"), f"{path}: неизвестный вид отказа"
    # Контракт обязан существовать: переименовали — случай надо перенести, а
    # не оставить висеть на имени, которого нет.
    get_contract(case["contract"])


@pytest.mark.parametrize("path", CASES, ids=_ident)
def test_reply_parses_the_same_way_it_did_live(path: Path) -> None:
    """Тот же текст → те же данные. Или тот же отказ того же вида."""
    case = json.loads(path.read_text(encoding="utf-8"))
    contract = get_contract(case["contract"])
    expect = case["expect"]

    if not expect["ok"]:
        with pytest.raises(LlmContractError) as exc:
            contract.parse(case["reply"])
        assert exc.value.kind == expect["kind"], (
            f"{_ident(path)}: ждали отказ вида {expect['kind']}, получили {exc.value.kind}. "
            f"Контракт стал строже или мягче — это и есть то, ради чего голден живёт."
        )
        return

    parsed = contract.parse(case["reply"])
    actual = json.loads(parsed.payload.model_dump_json(by_alias=True))
    assert actual == expect["payload"], (
        f"{_ident(path)}: разбор живого ответа изменился. Если это осознанная "
        f"правка контракта — обновить голден: python3 scripts/goldens.py bless {path}"
    )


@pytest.mark.parametrize("path", CASES, ids=_ident)
def test_parsing_is_stable_across_calls(path: Path) -> None:
    """Два разбора одного текста дают один результат.

    Проверка не про случайность — её в разборе нет, — а про состояние: модели
    Pydantic и реестр контрактов живут в модуле, и правка, которая начнёт
    накапливать что-нибудь между вызовами, проявится именно здесь.
    """
    case = json.loads(path.read_text(encoding="utf-8"))
    if not case["expect"]["ok"]:
        pytest.skip("случай про отказ — сравнивать нечего")
    contract = get_contract(case["contract"])
    first = contract.parse(case["reply"]).payload.model_dump_json(by_alias=True)
    second = contract.parse(case["reply"]).payload.model_dump_json(by_alias=True)
    assert first == second


def test_every_contract_in_the_registry_has_a_case() -> None:
    """Контракт без голдена — контракт, чью работу никто не проверял на живом.

    Тест намеренно не падает на неполноте, а перечисляет пробелы: собрать
    корпус на все двенадцать контрактов разом нельзя — для этого нужен прогон,
    который дошёл до каждого шага. Список пробелов — рабочий план, а красный
    гейт на нём отключили бы в первый же день.
    """
    from app.contracts import _REGISTRY

    covered = {p.parent.name for p in CASES}
    missing = sorted(set(_REGISTRY) - covered)
    if missing:
        pytest.skip(f"без голденов пока {len(missing)} контрактов: {', '.join(missing)}")
