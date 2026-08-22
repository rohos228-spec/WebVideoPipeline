"""Храповик по падениям тестов (scripts/pytest_ratchet.py).

Гейт «должно быть зелено» здесь невозможен: промт-библиотека вне git, и вне
машины владельца часть тестов падает на отсутствии данных. Храповик ловит
ровно то, ради чего гейт нужен — рост красноты.

Логика проверяется на синтетическом выводе pytest: гонять настоящую суиту
внутри суиты нельзя.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from app.project_root import find_project_root

_SCRIPT = find_project_root() / "scripts" / "pytest_ratchet.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("pytest_ratchet", _SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["pytest_ratchet"] = module
    spec.loader.exec_module(module)
    return module


ratchet = _load_module()

_OUTPUT = """\
....F...F                                                     [100%]
=========================== short test summary info ============================
FAILED tests/test_a.py::test_one - AssertionError
FAILED tests/test_b.py::test_two
ERROR tests/test_c.py::test_three - fixture 'x' not found
2 failed, 7 passed in 1.23s
"""


@pytest.fixture
def baseline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "pytest-failures.txt"
    monkeypatch.setattr(ratchet, "BASELINE", path)
    return path


def _fake_run(output: str, code: int = 1):
    def run(_extra):
        return (code, output)

    return run


def test_parses_failed_and_error_lines() -> None:
    """ERROR (упавшая фикстура) — тоже падение, иначе оно проедет мимо гейта."""
    found = set(ratchet._FAILED_RE.findall(_OUTPUT))
    assert found == {
        "tests/test_a.py::test_one",
        "tests/test_b.py::test_two",
        "tests/test_c.py::test_three",
    }


def test_new_failure_fails_the_gate(baseline: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    baseline.write_text("tests/test_a.py::test_one\n", encoding="utf-8")
    monkeypatch.setattr(ratchet, "_run_pytest", _fake_run(_OUTPUT))
    monkeypatch.setattr(sys, "argv", ["pytest_ratchet.py"])

    assert ratchet.main() == 1
    out = capsys.readouterr().out
    assert "НОВЫЕ падения (2)" in out
    assert "tests/test_b.py::test_two" in out


def test_known_failures_pass_the_gate(baseline: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    baseline.write_text(
        "tests/test_a.py::test_one\ntests/test_b.py::test_two\ntests/test_c.py::test_three\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(ratchet, "_run_pytest", _fake_run(_OUTPUT))
    monkeypatch.setattr(sys, "argv", ["pytest_ratchet.py"])

    assert ratchet.main() == 0
    assert "новых падений нет" in capsys.readouterr().out


def test_fixed_test_is_reported_but_baseline_not_narrowed(
    baseline: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """Сужение базы должно быть видно в диффе — само не происходит."""
    before = (
        "tests/test_a.py::test_one\n"
        "tests/test_b.py::test_two\n"
        "tests/test_c.py::test_three\n"
        "tests/test_d.py::test_fixed\n"
    )
    baseline.write_text(before, encoding="utf-8")
    monkeypatch.setattr(ratchet, "_run_pytest", _fake_run(_OUTPUT))
    monkeypatch.setattr(sys, "argv", ["pytest_ratchet.py"])

    assert ratchet.main() == 0
    out = capsys.readouterr().out
    assert "стало зелёным (1)" in out
    assert "tests/test_d.py::test_fixed" in out
    assert baseline.read_text(encoding="utf-8") == before


def test_update_rewrites_baseline(baseline: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ratchet, "_run_pytest", _fake_run(_OUTPUT))
    monkeypatch.setattr(sys, "argv", ["pytest_ratchet.py", "--update"])

    assert ratchet.main() == 0
    body = baseline.read_text(encoding="utf-8")
    assert "tests/test_a.py::test_one" in body
    assert body.startswith("#"), "нужна шапка с объяснением, иначе файл читается как список багов"


def test_comments_and_blanks_ignored_in_baseline(baseline: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    baseline.write_text(
        "# комментарий\n\ntests/test_a.py::test_one\ntests/test_b.py::test_two\n"
        "tests/test_c.py::test_three\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(ratchet, "_run_pytest", _fake_run(_OUTPUT))
    monkeypatch.setattr(sys, "argv", ["pytest_ratchet.py"])
    assert ratchet.main() == 0


def test_pytest_crash_is_not_reported_as_green(
    baseline: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """Код 4 (ошибка использования) — прогон не состоялся, а не «падений нет»."""
    monkeypatch.setattr(ratchet, "_run_pytest", _fake_run("usage error", code=4))
    monkeypatch.setattr(sys, "argv", ["pytest_ratchet.py"])
    assert ratchet.main() == 2


def test_all_green_run_passes(baseline: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    baseline.write_text("tests/test_a.py::test_one\n", encoding="utf-8")
    monkeypatch.setattr(ratchet, "_run_pytest", _fake_run("9 passed in 1.0s\n", code=0))
    monkeypatch.setattr(sys, "argv", ["pytest_ratchet.py"])
    assert ratchet.main() == 0
