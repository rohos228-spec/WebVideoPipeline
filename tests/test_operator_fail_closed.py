"""Fail-closed: без API-ключа check-роли НЕ зеленеют stub-вердиктом.

Контекст: раньше при отсутствии ключа `_stub_analysis` возвращал `pass`
(fail-open) — галлюцинированный [ok] фиксировался как принятый контент.
Теперь stub-путь для проверок доступен только по явному опт-ину
VP_ALLOW_STUB_CHECKS (в тестах включён autouse-фикстурой conftest).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.services.gpt_operator_client import run_operator_api
from app.settings import settings


def _call(tmp_path: Path, *, role: str, check_mode: bool = False):
    src = tmp_path / "project.xlsx"
    src.write_bytes(b"PK" + b"0" * 200)
    return asyncio.run(
        run_operator_api(
            project_dir=tmp_path,
            node_key="n_excel_gpt_1",
            role=role,
            output_mode="text",
            prompt="check",
            accompanying="go",
            input_paths=[src],
            check_mode=check_mode,
        )
    )


@pytest.mark.parametrize("role", ["review", "gate", "compare"])
def test_no_key_check_role_raises(tmp_path: Path, monkeypatch, role: str) -> None:
    monkeypatch.setattr("app.services.gpt_api.gpt_api_enabled", lambda: False)
    monkeypatch.setattr(settings, "allow_stub_checks", False)
    with pytest.raises(RuntimeError, match="fail-closed"):
        _call(tmp_path, role=role)


def test_no_key_check_mode_raises(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("app.services.gpt_api.gpt_api_enabled", lambda: False)
    monkeypatch.setattr(settings, "allow_stub_checks", False)
    with pytest.raises(RuntimeError, match="fail-closed"):
        _call(tmp_path, role="generate", check_mode=True)


def test_no_key_non_check_role_keeps_stub(tmp_path: Path, monkeypatch) -> None:
    """Генеративные роли без ключа по-прежнему идут в dev-stub (не проверка)."""
    monkeypatch.setattr("app.services.gpt_api.gpt_api_enabled", lambda: False)
    monkeypatch.setattr(settings, "allow_stub_checks", False)
    res = _call(tmp_path, role="generate")
    assert res.gate_status is None


def test_no_key_check_with_optin_stays_stub(tmp_path: Path, monkeypatch) -> None:
    """Явный опт-ин (dev/tests) сохраняет прежнее stub-поведение."""
    monkeypatch.setattr("app.services.gpt_api.gpt_api_enabled", lambda: False)
    monkeypatch.setattr(settings, "allow_stub_checks", True)
    res = _call(tmp_path, role="review")
    assert res.gate_status == "pass"
