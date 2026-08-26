"""Тайминги монтажа без книги проекта.

Последний шаг живого прогона 2026-08-26 — сборка — упал трижды подряд:

    не удалось записать R15 в project.xlsx — закрой Excel

Excel не открыт ни у кого: его не существует. При учётных записях книга не
пишется (`settings.xlsx_enabled`), а сборка была устроена так: посчитать
тайминги из ASR → записать в строку 15 книги → перечитать книгу с диска.
«Единственный источник таймингов монтажа — строка 15». Без файла цикл рвётся
на записи, и готовый ролик — 6 героев, 6 предметов, 12 кадров, 12 клипов,
озвучка, звуки — не собирается.

Теперь без книги `ensure_r15_from_asr` отдаёт посчитанные тайминги, а
`require_assembly_timeline_from_excel` берёт переданные `ts_cells` вместо
перечитывания. В базу они попадают дальше по сборке (Frame.start_ts/end_ts).
Режим владельца не тронут: с книгой всё как прежде, включая отказ без файла.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services import plan_timestamps as pt

FRAMES = [1, 2, 3]
CELLS = [(1, "первый кадр"), (2, "второй кадр"), (3, "третий кадр")]
TS_CELLS = [(1, "0:00.00-0:03.00"), (2, "0:03.00-0:06.50"), (3, "0:06.50-0:10.00")]


def _project(tmp_path):
    return SimpleNamespace(id=7, slug="rolik", data_dir=tmp_path, meta={})


@pytest.fixture
def voice(tmp_path):
    f = tmp_path / "voice_full.mp3"
    f.write_bytes(b"ID3")
    return f


@pytest.mark.asyncio
async def test_timeline_from_passed_cells_when_workbook_is_off(tmp_path, voice, monkeypatch):
    from app.settings import settings

    monkeypatch.setattr(settings, "xlsx_write", False)
    monkeypatch.setattr(pt, "probe_duration", lambda *_a, **_k: _coro(10.0))

    result = await pt.require_assembly_timeline_from_excel(
        _project(tmp_path), FRAMES, CELLS, voice, ts_cells=TS_CELLS, ts_row=15
    )
    clips = result[0]
    assert clips is not None and len(clips) == 3
    assert abs(clips[-1].end_ts - 10.0) < 1e-6
    assert not (tmp_path / "project.xlsx").exists(), "книга не должна появиться"


@pytest.mark.asyncio
async def test_workbook_off_without_cells_is_an_honest_error(tmp_path, voice, monkeypatch):
    from app.settings import settings

    monkeypatch.setattr(settings, "xlsx_write", False)
    with pytest.raises(RuntimeError, match="не переданы"):
        await pt.require_assembly_timeline_from_excel(_project(tmp_path), FRAMES, CELLS, voice)


@pytest.mark.asyncio
async def test_owner_mode_still_requires_the_workbook(tmp_path, voice, monkeypatch):
    """С книгой — прежнее поведение: нет файла → отказ, переданное не подставляется."""
    from app.settings import settings

    monkeypatch.setattr(settings, "xlsx_write", True)
    with pytest.raises(RuntimeError, match="нет файла"):
        await pt.require_assembly_timeline_from_excel(
            _project(tmp_path), FRAMES, CELLS, voice, ts_cells=TS_CELLS, ts_row=15
        )


@pytest.mark.asyncio
async def test_ensure_r15_returns_ranges_without_writing(tmp_path, voice, monkeypatch):
    """Без книги тайминги считаются и возвращаются; запись в книгу не вызывается."""
    from app.settings import settings

    monkeypatch.setattr(settings, "xlsx_write", False)
    monkeypatch.setattr(pt, "probe_duration", lambda *_a, **_k: _coro(10.0))

    fake_clips = [
        SimpleNamespace(frame_number=1, start_ts=0.0, end_ts=3.0, duration=3.0),
        SimpleNamespace(frame_number=2, start_ts=3.0, end_ts=6.5, duration=3.5),
        SimpleNamespace(frame_number=3, start_ts=6.5, end_ts=10.0, duration=3.5),
    ]
    monkeypatch.setattr("app.services.frame_audio.frame_clips_from_whisper", lambda *_a, **_k: fake_clips)
    monkeypatch.setattr("app.services.mapper.timings_have_crumb_durations", lambda *_a, **_k: False)

    def _no_write(*_a, **_k):
        raise AssertionError("без книги запись в project.xlsx не должна вызываться")

    monkeypatch.setattr("app.storage.plan_sheet_v8.write_plan_timestamps", _no_write)
    monkeypatch.setattr("app.storage.plan_sheet_v8.write_plan_durations", _no_write)

    ranges, ts_row = await pt.ensure_r15_from_asr(
        _project(tmp_path), frame_numbers=FRAMES, cells=CELLS, words=[object()], voice_full_path=voice
    )
    assert [n for n, _ in ranges] == FRAMES
    assert all(pt.parse_timecode_range(label) for _, label in ranges), "метки должны быть разбираемыми"


async def _coro(value):
    return value
