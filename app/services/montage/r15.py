"""Чтение меток R15 из Excel — только строка 15, лист «план»."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from loguru import logger

from app.models import Project
from app.services.plan_timestamps import count_parsed_timestamp_cells, parse_timecode_range
from app.storage.plan_sheet_v8 import read_plan_timestamps_cells, scan_r15_frame_numbers


@dataclass(frozen=True)
class R15Marker:
    frame_number: int
    label: str
    start_s: float
    end_s: float

    @property
    def duration_s(self) -> float:
        return self.end_s - self.start_s


def excel_frame_numbers(project: Project) -> list[int]:
    """Кадры по непустым ячейкам R15."""
    return scan_r15_frame_numbers(project)


# Стык кадров от выравнивания по словам гуляет на десятки миллисекунд.
# Больше — это уже не погрешность, а сломанный порядок.
_MAX_CLAMPABLE_OVERLAP_S = 0.35


def db_markers(frames: list[Any]) -> list[R15Marker]:
    """Тайминг монтажа из базы: ``Frame.start_ts/end_ts``.

    SoT = База, xlsx — экспорт. Лист «план» держит по одной колонке на ячейку
    закадра (13 на этом проекте), а развёртка сцены в шоты живёт только в БД
    (24 кадра). Пока монтаж читал R15, веер схлопывался обратно: в ролик
    попадали 13 клипов из 24, метки кончались на 37.9 с при озвучке 70.0 —
    и последние 32 секунды крутился один и тот же кадр.

    Кадры без тайминга пропускаем: выравнивание не дало им времени, и
    нулевой сегмент монтажу не нужен. Молча не пропускаем — пишем в лог.
    """
    rows: list[tuple[int, float, float]] = []
    skipped: list[int] = []
    for fr in frames or []:
        num = int(getattr(fr, "number", 0) or 0)
        start = getattr(fr, "start_ts", None)
        end = getattr(fr, "end_ts", None)
        if not num or start is None or end is None:
            if num:
                skipped.append(num)
            continue
        s, e = float(start), float(end)
        if e - s <= 0.01:
            skipped.append(num)
            continue
        rows.append((num, s, e))
    if skipped:
        logger.warning("montage: кадры без тайминга в БД пропущены — {}", skipped[:12])
    rows.sort(key=lambda r: (r[1], r[0]))

    markers: list[R15Marker] = []
    prev_end = -0.01
    for num, s, e in rows:
        overlap = prev_end - s
        if overlap > _MAX_CLAMPABLE_OVERLAP_S:
            # Настоящий разлад порядка — тайминг не наш, пусть решает xlsx.
            logger.warning(
                "montage: кадр {} стартует {:.3f}s при конце предыдущего {:.3f}s — "
                "перехлёст {:.2f}s больше допустимого, тайминг БД не берём",
                num,
                s,
                prev_end,
                overlap,
            )
            return []
        if overlap > 0:
            # Выравнивание по словам даёт стыки с точностью до десятков мс;
            # ронять из-за них весь тайминг базы нельзя — прижимаем встык.
            # Живой прогон: кадры 13→14 разошлись на 0.05 с, и монтаж молча
            # уехал на устаревший R15 из экселя.
            s = prev_end
        if e - s <= 0.01:
            skipped.append(num)
            continue
        markers.append(R15Marker(frame_number=num, label=_label(s, e), start_s=s, end_s=e))
        prev_end = e
    return markers


def _label(start_s: float, end_s: float) -> str:
    def mmss(x: float) -> str:
        m = int(x) // 60
        return f"{m}:{x - 60 * m:05.2f}"

    return f"{mmss(start_s)}-{mmss(end_s)}"


def resolve_montage_frame_numbers(
    project: Project,
    db_frame_numbers: list[int],
) -> list[int]:
    """Монтаж по R15: Excel берём, ТОЛЬКО если меток там больше, чем кадров в БД.

    Раньше Excel брался всегда, когда строка R15 непуста, — и при 13 метках
    против 24 кадров одиннадцать кадров молча выпадали из ролика. Докстринг
    описывал верное поведение, код делал другое.
    """
    r15_nums = scan_r15_frame_numbers(project)
    if not r15_nums:
        return db_frame_numbers
    if len(r15_nums) < len(db_frame_numbers):
        logger.warning(
            "[#{}] R15 scan: {} меток против {} кадров БД — Excel устарел, монтаж по БД",
            project.id,
            len(r15_nums),
            len(db_frame_numbers),
        )
        return db_frame_numbers
    if len(r15_nums) > len(db_frame_numbers):
        logger.warning(
            "[#{}] R15 scan: {} меток, БД {} кадров — монтаж по R15",
            project.id,
            len(r15_nums),
            len(db_frame_numbers),
        )
    return r15_nums


def r15_cells_monotonic(
    ts_cells: list[tuple[int, str]],
    *,
    tolerance: float = 0.02,
) -> bool:
    """True если метки R15 идут по шкале без overlap назад."""
    prev_end = -0.01
    for _num, label in ts_cells:
        parsed = parse_timecode_range(label)
        if parsed is None:
            return False
        start, end = parsed
        if end <= start + 0.01:
            return False
        if start < prev_end - tolerance:
            return False
        prev_end = end
    return True


def load_r15_markers(project: Project, frame_numbers: list[int]) -> tuple[list[R15Marker], int]:
    """Каждый запуск: свежее чтение project.xlsx с диска."""
    xlsx = project.data_dir / "project.xlsx"
    if not xlsx.is_file():
        raise RuntimeError(f"нет {xlsx}")

    ts_cells, ts_row = read_plan_timestamps_cells(project, frame_numbers)
    st = xlsx.stat()
    logger.info(
        "[#{}] R{} read {} mtime={} size={}",
        project.id,
        ts_row,
        xlsx,
        datetime.fromtimestamp(st.st_mtime, tz=UTC).isoformat(),
        st.st_size,
    )

    _filled, parsed_n, bad = count_parsed_timestamp_cells(ts_cells)
    if parsed_n != len(frame_numbers):
        sample = ", ".join(str(n) for n in bad[:8]) if bad else "—"
        raise RuntimeError(
            f"R{ts_row}: прочитано {parsed_n}/{len(frame_numbers)} меток. "
            f"Сохрани Excel, закрой Office. Битые: {sample}"
        )

    markers: list[R15Marker] = []
    prev_end = -0.01
    for num, label in ts_cells:
        parsed = parse_timecode_range(label)
        if parsed is None:
            raise RuntimeError(f"кадр {num}: битая метка {label!r}")
        start, end = parsed
        if end <= start + 0.01:
            raise RuntimeError(f"кадр {num}: end<=start ({label!r})")
        if start < prev_end - 0.02:
            raise RuntimeError(f"кадр {num}: start {start:.3f}s < prev {prev_end:.3f}s — метки не по порядку")
        markers.append(R15Marker(frame_number=num, label=label.strip(), start_s=start, end_s=end))
        prev_end = end

    return markers, ts_row


def write_r15_proof(markers: list[R15Marker], path: Path, *, ts_row: int | None, voice_s: float) -> None:
    lines = [
        f"source=excel_r15_row_{ts_row}" if ts_row else "source=db_frames_start_end_ts",
        f"markers={len(markers)}",
        f"voice_duration={voice_s:.3f}",
        f"last_marker_end={markers[-1].end_s:.3f}" if markers else "last_marker_end=0",
        f"voice_gap={voice_s - markers[-1].end_s:.3f}" if markers else "",
        "",
        "frame\texcel\tstart_s\tend_s\tduration_s",
    ]
    for m in markers:
        lines.append(f"{m.frame_number}\t{m.label}\t{m.start_s:.3f}\t{m.end_s:.3f}\t{m.duration_s:.3f}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
