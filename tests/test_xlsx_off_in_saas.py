"""Excel в SaaS: книга не пишется, данные из неё читаются.

Книга проекта существует ради человека за той же машиной — открыть,
поправить, сохранить и закрыть (`docs/SAAS-PIVOT.md` §9.3). У клиента студии
такого человека нет: некому открыть, некому закрыть. При этом openpyxl
перечитывает и переписывает книгу целиком на каждую ячейку, то есть на каждом
из двадцати четырёх кадров тратится диск и время на файл, который никто не
откроет.

Развязка сделана выключением записи, а не переписыванием шестидесяти семи
файлов. Это работает потому, что шаги УЖЕ спрашивают сначала базу, а в книгу
идут запасным путём: `generate_images._load_refs_for_frame` читает xlsx,
только если в базе рефов нет. Значит выключение записи ничего не ломает, а
переписывание можно вести постепенно и не под запуск.
"""

from __future__ import annotations

import pytest

from app.settings import settings
from app.storage.project_sheet import ProjectSheet


@pytest.fixture
def sheet(tmp_path):
    return ProjectSheet(file_path=tmp_path / "project.xlsx")


def test_owner_mode_writes_the_workbook(sheet, monkeypatch) -> None:
    """На машине владельца книга остаётся: это его рабочий инструмент."""
    monkeypatch.setattr(settings, "studio_session_secret", "")
    monkeypatch.setattr(settings, "xlsx_write", None)
    assert sheet.writable
    sheet.ensure_initialized(project_id=1, slug="owner-film")
    assert sheet.file_path.is_file()


def test_saas_writes_nothing_to_disk(sheet, monkeypatch) -> None:
    """В SaaS файла не появляется вовсе.

    Не «появляется пустой» и не «появляется и не обновляется»: пустая книга
    на диске выглядит как испорченная и однажды приведёт кого-нибудь к мысли
    её починить.
    """
    monkeypatch.setattr(settings, "studio_session_secret", "секрет-длиною-в-тридцать-два-байта-точно")
    monkeypatch.setattr(settings, "xlsx_write", None)
    assert not sheet.writable
    path = sheet.ensure_initialized(project_id=1, slug="saas-film")
    assert path == sheet.file_path, "путь обязан вернуться: от него строят другие пути"
    assert not sheet.file_path.exists()


def test_explicit_setting_beats_the_mode(sheet, monkeypatch) -> None:
    """Явное значение перекрывает режим — в обе стороны.

    Нужно и для отладки SaaS у себя, и для владельца, который захочет
    перестать плодить книги, не переезжая в SaaS.
    """
    monkeypatch.setattr(settings, "studio_session_secret", "секрет-длиною-в-тридцать-два-байта-точно")
    monkeypatch.setattr(settings, "xlsx_write", True)
    assert sheet.writable

    monkeypatch.setattr(settings, "studio_session_secret", "")
    monkeypatch.setattr(settings, "xlsx_write", False)
    assert not sheet.writable


def test_save_is_the_last_line_of_defence(sheet, monkeypatch) -> None:
    """Запись, пришедшая мимо публичных методов, тоже не доедет до диска.

    Точек записи в книгу шестьдесят семь файлов; закрывать каждую значит
    закрыть шестьдесят шесть и забыть одну. Поэтому выключение стоит и на
    самом сохранении.
    """
    monkeypatch.setattr(settings, "studio_session_secret", "")
    monkeypatch.setattr(settings, "xlsx_write", True)
    sheet.ensure_initialized(project_id=1, slug="film")
    assert sheet.file_path.is_file()
    before = sheet.file_path.read_bytes()

    monkeypatch.setattr(settings, "xlsx_write", False)

    class _Book:
        def save(self, path):  # pragma: no cover — до сюда доходить не должно
            raise AssertionError("книга сохранена при выключенной записи")

    sheet._save(_Book())
    assert sheet.file_path.read_bytes() == before
