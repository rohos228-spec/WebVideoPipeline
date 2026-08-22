"""Прогон тестов не должен писать в рабочее дерево репозитория.

История. До 2026-08-22 суита дописывала настоящие `logs/status.log`,
`logs/errors.log` и плодила каталоги в `data/library/old` (накопилось 136 от
чужих прогонов). Причина в обоих случаях одна: путь считался константой на
импорте модуля, а autouse-фикстура `tests/conftest.py` подменяет
`settings.data_dir` уже после — подмену эти константы не видели.

Тесты ниже фиксируют контракт: любой путь, куда пишет код во время прогона,
берётся из `settings` в момент вызова и лежит под tmp.
"""

from __future__ import annotations

from pathlib import Path

from app.project_root import find_project_root
from app.settings import settings

REPO_ROOT = find_project_root()


def _under(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return True


def test_logs_dir_is_isolated(tmp_path_factory) -> None:
    """status.log / errors.log — под tmp, не в logs/ репозитория."""
    from app.services.log_paths import errors_log_path, status_log_path

    for path in (status_log_path(), errors_log_path()):
        assert not _under(path, REPO_ROOT / "logs"), path
        assert _under(path, Path(settings.logs_dir)), path


def test_library_roots_follow_settings() -> None:
    """local_library считает корни на вызове, а не на импорте.

    Сравниваем с настоящим `data/library`, а не с `data/` целиком: TMPDIR
    процесса указывает в `data/.cache/temp` (`nvidia_asr_env`), поэтому
    tmp_path тестов формально лежит под `data/` — проверка «не под data»
    была бы всегда красной.
    """
    from app.services.local_library import library_roots

    real_library = REPO_ROOT / "data" / "library"
    roots = library_roots()
    assert roots["root"] == Path(settings.data_dir) / "library"
    for name, path in roots.items():
        assert not _under(path, real_library), f"{name} → {path}"
        assert path.resolve() != real_library.resolve(), name


def test_library_roots_react_to_data_dir_change(monkeypatch, tmp_path) -> None:
    """Смена data_dir на лету двигает корни библиотеки (константа бы не сдвинулась)."""
    from app.services.local_library import library_roots, old_root

    moved = tmp_path / "elsewhere"
    monkeypatch.setattr(settings, "data_dir", moved)

    assert library_roots()["root"] == moved / "library"
    assert old_root() == moved / "library" / "old"


def test_status_log_write_stays_in_tmp() -> None:
    """Реальная запись строки статуса не трогает logs/ репозитория."""
    from app.services import node_status_machine as nsm
    from app.services.log_paths import status_log_path

    repo_log = REPO_ROOT / "logs" / "status.log"
    before = repo_log.stat().st_mtime_ns if repo_log.exists() else None

    nsm._write_status_log("test\tisolation\tprobe")

    written = status_log_path()
    assert written.is_file()
    assert "isolation" in written.read_text(encoding="utf-8")

    after = repo_log.stat().st_mtime_ns if repo_log.exists() else None
    assert after == before, "прогон дописал реальный logs/status.log"
