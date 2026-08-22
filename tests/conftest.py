"""Общие фикстуры тестов.

W1-fix п.7: центральный harness-гейт (auto_advance) включён по умолчанию,
чтобы CI ловил регрессии данных. Тест, которому нужен gate OFF (например,
механика продвижения без on-disk артефактов), помечается
``@pytest.mark.no_harness_gate`` — см. ``pyproject.toml`` pytest markers.

Каждый тест получает свой ``settings.data_dir`` / ``sqlite_path`` /
``logs_dir`` в tmp — иначе slug'и вроде ``vo-test``/``store`` делят
/workspace/data и сыплют соседей (FileExistsError, stale voiceover.txt).

``logs_dir`` добавлен 2026-08-22: до этого писатели журналов держали
``Path("logs/errors.log")`` константой от CWD, и прогон дописывал реальные
``logs/status.log`` и ``logs/errors.log`` репозитория. То же было с
``data/library/old`` — ``local_library`` считал корни на импорте, мимо
подмены ``data_dir`` (накопилось 136 каталогов от чужих прогонов).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.settings import settings

_ORIG_DATA_DIR = Path(settings.data_dir)
_ORIG_SQLITE_PATH = Path(settings.sqlite_path)
_ORIG_LOGS_DIR = Path(settings.logs_dir)


@pytest.fixture(autouse=True)
def _isolate_settings_paths(tmp_path_factory, monkeypatch, request):
    # W1-fix п.7: гейт включён по умолчанию (False). Тест может явно
    # отключить через маркер no_harness_gate.
    if "no_harness_gate" in request.keywords:
        monkeypatch.setattr(settings, "harness_gate_disabled", True)
    else:
        monkeypatch.setattr(settings, "harness_gate_disabled", False)
    # Stub-вердикты проверок без API-ключа: в проде fail-closed (RuntimeError),
    # тестам stub-путь нужен явно.
    monkeypatch.setattr(settings, "allow_stub_checks", True)
    root = tmp_path_factory.mktemp("vp-isol")
    monkeypatch.setattr(settings, "data_dir", root)
    monkeypatch.setattr(settings, "sqlite_path", root / "state.db")
    monkeypatch.setattr(settings, "logs_dir", root / "logs")
    yield
    # На случай прямой записи settings.data_dir = ... в обход monkeypatch.
    settings.data_dir = _ORIG_DATA_DIR
    settings.sqlite_path = _ORIG_SQLITE_PATH
    settings.logs_dir = _ORIG_LOGS_DIR
