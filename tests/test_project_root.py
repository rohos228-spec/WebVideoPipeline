"""Пути от корня репозитория не зависят от CWD (например web/ после npm build)."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.project_root import find_project_root, resolve_project_path


def test_find_project_root_has_pyproject() -> None:
    root = find_project_root()
    assert (root / "pyproject.toml").is_file()


def test_resolve_project_path_from_subdirectory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = find_project_root()
    sub = root / "web"
    sub.mkdir(exist_ok=True)
    monkeypatch.chdir(sub)
    resolved = resolve_project_path(Path("./data/state.db"))
    assert resolved == root / "data" / "state.db"


def test_settings_db_not_under_web(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = find_project_root()
    sub = root / "web"
    sub.mkdir(exist_ok=True)
    monkeypatch.chdir(sub)
    # Перезагрузка settings после смены CWD
    import importlib

    import app.settings as settings_mod

    original = settings_mod.settings
    try:
        importlib.reload(settings_mod)
        db = settings_mod.settings.db_url
        assert "/web/data/" not in db.replace("\\", "/")
        assert "state.db" in db
    finally:
        # Reload подменяет `app.settings.settings` НОВЫМ объектом, а
        # autouse-фикстура conftest монкипатчит тот, что был. Модули,
        # которые импортируют settings внутри функции (их много —
        # provider_breaker, media_ledger, local_library…), после этого
        # читают неподменённый объект, и следующие тесты в прогоне
        # тихо получают продовые значения вместо тестовых.
        # Именно так ломались 5 тестов provider_breaker: по одному
        # зелёные, в полной суите — красные.
        settings_mod.settings = original
