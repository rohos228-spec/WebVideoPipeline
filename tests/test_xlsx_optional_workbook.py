"""Книга проекта не обязана существовать — конвейер работает без неё.

`project.xlsx` в этом проекте — deprecated fallback: контракт шага это
apply-ops в базу (`docs/PROMPT_CONTRACT.md`). Там, где заведены учётные записи,
`settings.xlsx_enabled` выключает запись книги совсем: открыть файл на диске
узла всё равно некому.

Проверять это надо отдельным тестом, потому что режимов два, а гоняется обычно
один. У владельца учётных записей нет, книга пишется, файл на месте — и
требование «файл обязан существовать» выглядит выполненным всегда. На сервере с
учётными записями оно не выполняется никогда, и первый же шаг падает:

    FileNotFoundError: project.xlsx не найден: …/project.xlsx

Ровно так и случилось на боевом прогоне 2026-08-25: проект ушёл в паузу на три
цикла по тридцать минут, а причина выглядела как отсутствующий файл данных,
хотя на самом деле файл и не должен был появиться.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.xlsx_step_runners import _ensure_project_xlsx


def _project(tmp_path, slug="rolik"):
    """Достаточно того, чем пользуется `_ensure_project_xlsx`."""
    return SimpleNamespace(id=1, slug=slug, data_dir=tmp_path / "videos" / slug, meta={})


def test_no_workbook_when_writing_is_off(tmp_path, monkeypatch):
    """Запись выключена — путь возвращается, файла нет, отказа нет."""
    from app.settings import settings

    monkeypatch.setattr(settings, "xlsx_write", False)

    path = _ensure_project_xlsx(_project(tmp_path))

    assert path.name == "project.xlsx"
    assert not path.exists(), "книга не должна создаваться при выключенной записи"


def test_project_dir_is_created_even_without_workbook(tmp_path, monkeypatch):
    """Каталог проекта нужен всегда: от него считаются voiceover.txt и прочее.

    `run_script_xlsx` пишет `proj_xlsx.parent / "voiceover.txt"`. В свежем
    проекте каталога ещё нет, и без явного `mkdir` шаг падал бы уже на записи
    закадрового текста — то есть на шаг позже и с другой ошибкой.
    """
    from app.settings import settings

    monkeypatch.setattr(settings, "xlsx_write", False)

    path = _ensure_project_xlsx(_project(tmp_path))

    assert path.parent.is_dir()
    # И запись рядом действительно проходит.
    (path.parent / "voiceover.txt").write_text("проверка", encoding="utf-8")


def test_workbook_is_created_when_writing_is_on(tmp_path, monkeypatch):
    """Обратная сторона: при включённой записи книга обязана появиться."""
    from app.settings import settings

    monkeypatch.setattr(settings, "xlsx_write", True)

    path = _ensure_project_xlsx(_project(tmp_path))

    assert path.is_file(), "при включённой записи книга должна быть создана"


def test_existing_workbook_is_returned_as_is(tmp_path, monkeypatch):
    """Уже существующая книга не пересоздаётся и не затирается."""
    from app.settings import settings

    monkeypatch.setattr(settings, "xlsx_write", False)

    project = _project(tmp_path)
    project.data_dir.mkdir(parents=True, exist_ok=True)
    existing = project.data_dir / "project.xlsx"
    existing.write_text("это не настоящий xlsx, но трогать его нельзя", encoding="utf-8")

    path = _ensure_project_xlsx(project)

    assert path == existing
    assert existing.read_text(encoding="utf-8").startswith("это не настоящий")


def test_failure_to_create_still_raises(tmp_path, monkeypatch):
    """Отказ остаётся там, где он осмыслен: книга должна была появиться и нет.

    Это настоящая поломка — испорченный шаблон, нет прав на запись. Отличать
    её от «книга выключена настройкой» и есть весь смысл правки.
    """
    from app.settings import settings
    from app.storage import project_sheet

    monkeypatch.setattr(settings, "xlsx_write", True)

    project = _project(tmp_path)

    def _initialized_but_missing(self, *, project_id, slug):
        return project.data_dir / "project.xlsx"

    monkeypatch.setattr(project_sheet.ProjectSheet, "ensure_initialized", _initialized_but_missing)

    with pytest.raises(FileNotFoundError, match="project.xlsx"):
        _ensure_project_xlsx(project)
