"""Тайминг монтажа берётся из базы, xlsx — только экспорт.

Живой прогон 2026-08-23: озвучка 70.03 с, в базе 24 кадра с таймингом до
70.031, а в ролик попали 13 клипов и последние 32 секунды крутился один и
тот же кадр. Причина: монтаж читал строку R15 листа «план», а лист держит
по одной колонке на ячейку закадра — развёртки сцены в шоты там нет места.
"""

from __future__ import annotations

from types import SimpleNamespace

from app.services.montage.r15 import db_markers


def _frame(number: int, start: float | None, end: float | None) -> SimpleNamespace:
    return SimpleNamespace(number=number, start_ts=start, end_ts=end)


def test_markers_cover_every_shot_not_just_voiceover_cells() -> None:
    frames = [_frame(i, (i - 1) * 2.5, i * 2.5) for i in range(1, 25)]
    markers = db_markers(frames)
    assert len(markers) == 24
    assert markers[-1].end_s == 60.0
    assert [m.frame_number for m in markers] == list(range(1, 25))


def test_frames_without_timing_are_skipped_loudly_not_zeroed() -> None:
    """Выравнивание не дало кадру времени — нулевой сегмент монтажу не нужен."""
    frames = [
        _frame(1, 0.0, 2.0),
        _frame(2, None, None),
        _frame(3, 2.0, 4.0),
        _frame(4, 4.0, 4.005),
    ]
    markers = db_markers(frames)
    assert [m.frame_number for m in markers] == [1, 3]


def test_labels_are_montage_readable() -> None:
    markers = db_markers([_frame(1, 0.0, 2.92), _frame(2, 66.66, 70.03)])
    assert markers[0].label == "0:00.00-0:02.92"
    assert markers[1].label == "1:06.66-1:10.03"


def test_out_of_order_timing_falls_back_instead_of_breaking_the_cut() -> None:
    frames = [_frame(1, 0.0, 5.0), _frame(2, 1.0, 3.0)]
    assert db_markers(frames) == []


def test_empty_db_timeline_means_fallback() -> None:
    assert db_markers([]) == []
    assert db_markers([_frame(1, None, None)]) == []


def test_stale_excel_no_longer_wins_over_the_database() -> None:
    """Докстринг обещал брать Excel только при БОЛЬШЕМ числе меток.

    Код брал его всегда, и 13 меток против 24 кадров молча роняли одиннадцать.
    """
    import inspect

    from app.services.montage import r15

    src = inspect.getsource(r15.resolve_montage_frame_numbers)
    assert "len(r15_nums) < len(db_frame_numbers)" in src
    assert "return db_frame_numbers" in src


def test_montage_engine_asks_the_database_first() -> None:
    import inspect

    from app.services.montage import variant2

    src = inspect.getsource(variant2.run_variant2)
    assert "_load_markers_db_first" in src
    assert "load_r15_markers(project, frame_numbers)" not in src


def test_tiny_overlap_is_clamped_not_a_reason_to_drop_the_database() -> None:
    """Живой прогон: кадры 13→14 разошлись на 0.05 с, и монтаж уехал на xlsx.

    Выравнивание по словам даёт стыки с точностью до десятков миллисекунд.
    Ронять из-за них весь тайминг базы — значит никогда им не пользоваться.
    """
    frames = [_frame(13, 35.02, 37.93), _frame(14, 37.88, 44.18)]
    markers = db_markers(frames)
    assert len(markers) == 2
    assert markers[1].start_s == 37.93
    assert markers[0].end_s == markers[1].start_s


def test_real_disorder_still_falls_back() -> None:
    frames = [_frame(1, 0.0, 10.0), _frame(2, 5.0, 12.0)]
    assert db_markers(frames) == []
