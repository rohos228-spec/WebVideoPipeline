"""Штамп монтажа не требует книги проекта.

Живой прогон 2026-08-26: ролик собран до последнего шага — тайминги из базы,
метки из базы, — и сборка упала на `xlsx.stat()` ради информационной строки
`xlsx_mtime=` в MONTAGE_STAMP.txt. Шесть отказов, пауза. Книги при учётных
записях не существует, и штамп обязан это просто записать.
"""

from __future__ import annotations

from app.services.montage.variant2 import _xlsx_stamp_lines


def test_stamp_without_workbook_records_the_fact(tmp_path):
    lines = _xlsx_stamp_lines(tmp_path / "project.xlsx")
    assert lines[0].startswith("xlsx=")
    assert "книга выключена" in lines[1]


def test_stamp_with_workbook_keeps_mtime(tmp_path):
    f = tmp_path / "project.xlsx"
    f.write_bytes(b"PK")
    lines = _xlsx_stamp_lines(f)
    assert lines[1].startswith("xlsx_mtime=20")
