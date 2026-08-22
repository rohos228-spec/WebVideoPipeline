"""Пробы медиафайлов: ffprobe + дешёвые пре-чеки до платного vision.

Этап 4 (C.1): probe_image / probe_video / probe_audio_silence — каскад
«дёшево→дорого»: брак (нечитаемый файл, нулевая длительность, чужой
aspect, чёрный кадр, тишина) отсекается ДО LLM-vision. Пороги — константы
модуля (v1); порог «чёрного» — почти-абсолютный (кодек-брак, НЕ «тёмная
сцена»: у легитимной ночной сцены есть света). Стилл для проверки видео —
из СЕРЕДИНЫ клипа (первый кадр законно бывает чёрным на fade-in).
"""

from __future__ import annotations

import asyncio
import json
import re
import tempfile
from pathlib import Path

# Почти-абсолютные пороги «чёрного» (яркость 0..255).
BLACK_MEAN_MAX = 6.0
BLACK_PEAK_MAX = 24.0
# Допуск aspect (±5%).
ASPECT_TOLERANCE = 0.05
# Средняя громкость ниже — тишина (ffmpeg volumedetect, dBFS).
SILENCE_MEAN_DB = -55.0


class MediaProbeError(RuntimeError):
    """Брак по пре-чеку; ``code`` — машиночитаемая причина media_probe:<code>."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)

    @property
    def reason(self) -> str:
        return f"media_probe:{self.code}"


def _parse_aspect(expect: str | None) -> float | None:
    s = (expect or "").strip()
    if not s:
        return None
    m = re.match(r"^(\d+(?:\.\d+)?)\s*[:xX/]\s*(\d+(?:\.\d+)?)$", s)
    if not m:
        return None
    w, h = float(m.group(1)), float(m.group(2))
    if w <= 0 or h <= 0:
        return None
    return w / h


def _check_aspect(width: int, height: int, expect: str | None, path: Path) -> None:
    want = _parse_aspect(expect)
    if want is None or height <= 0:
        return
    got = width / height
    if abs(got - want) / want > ASPECT_TOLERANCE:
        raise MediaProbeError(
            "aspect_mismatch",
            f"{path.name}: aspect {width}x{height} ({got:.3f}) ≠ "
            f"проектного {expect} (допуск ±{ASPECT_TOLERANCE:.0%})",
        )


def _image_stats(path: Path) -> tuple[int, int, float, float]:
    """(width, height, mean, peak) яркости. Sync — звать через to_thread."""
    from PIL import Image

    with Image.open(path) as im:
        im = im.convert("L")
        w, h = im.size
        # 64px-миниатюра: статистика яркости без прохода по мегапикселям
        thumb = im.copy()
        thumb.thumbnail((64, 64))
        px = list(thumb.getdata())
    if not px:
        raise MediaProbeError("image_empty", f"{path.name}: пустое изображение")
    return w, h, sum(px) / len(px), float(max(px))


async def probe_image(path: Path, *, expect_aspect: str | None = None) -> tuple[int, int]:
    """PNG/JPEG: читается, ненулевой размер, aspect проекта, не-чёрный."""
    if not path.is_file() or path.stat().st_size == 0:
        raise MediaProbeError("image_unreadable", f"{path} отсутствует или пуст")
    try:
        w, h, mean, peak = await asyncio.to_thread(_image_stats, path)
    except MediaProbeError:
        raise
    except Exception as e:  # noqa: BLE001 — битый файл/не картинка
        raise MediaProbeError("image_unreadable", f"{path.name}: не читается ({e})") from e
    if w <= 0 or h <= 0:
        raise MediaProbeError("image_zero_size", f"{path.name}: размер {w}x{h}")
    _check_aspect(w, h, expect_aspect, path)
    if mean <= BLACK_MEAN_MAX and peak <= BLACK_PEAK_MAX:
        raise MediaProbeError(
            "black_image",
            f"{path.name}: чёрный кадр (mean={mean:.1f}, peak={peak:.0f})",
        )
    return w, h


async def _extract_probe_still(path: Path, at_sec: float, out: Path) -> None:
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-y",
        "-ss",
        f"{max(at_sec, 0.0):.3f}",
        "-i",
        str(path),
        "-frames:v",
        "1",
        "-q:v",
        "3",
        str(out),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _stdout, stderr = await proc.communicate()
    if proc.returncode != 0 or not out.is_file() or out.stat().st_size < 32:
        raise MediaProbeError(
            "video_unreadable",
            f"{path.name}: не извлекается кадр @{at_sec:.1f}s "
            f"({(stderr or b'').decode(errors='ignore')[:160]})",
        )


async def probe_video(
    path: Path,
    *,
    expect_aspect: str | None = None,
    check_black: bool = True,
) -> dict:
    """MP4: читается ffprobe, duration > 0, aspect, не-чёрная середина."""
    if not path.is_file() or path.stat().st_size == 0:
        raise MediaProbeError("video_unreadable", f"{path} отсутствует или пуст")
    try:
        w, h = await probe_video_size(path)
        dur = await probe_duration(path)
    except MediaProbeError:
        raise
    except Exception as e:  # noqa: BLE001 — RuntimeError ffprobe и пр.
        raise MediaProbeError("video_unreadable", f"{path.name}: ffprobe не читает ({e})") from e
    if dur <= 0.05:
        raise MediaProbeError("duration_zero", f"{path.name}: длительность {dur:.3f}s")
    _check_aspect(w, h, expect_aspect, path)
    if check_black:
        with tempfile.TemporaryDirectory(prefix="vp_probe_") as td:
            still = Path(td) / "mid.jpg"
            await _extract_probe_still(path, dur / 2.0, still)
            _w, _h, mean, peak = await asyncio.to_thread(_image_stats, still)
        if mean <= BLACK_MEAN_MAX and peak <= BLACK_PEAK_MAX:
            raise MediaProbeError(
                "black_video",
                f"{path.name}: чёрный кадр в середине клипа (mean={mean:.1f}, peak={peak:.0f})",
            )
    return {"width": w, "height": h, "duration": dur}


def stash_rejected_file(path: Path) -> bool:
    """Отбракованный приёмкой файл → <dir>/stale/ (не удаляем: форензика).

    Этап 4 (C.4, блокер панели): файл НЕ должен остаться на месте —
    «диск = истина» скипнул бы кадр и брак принялся бы recovery-путём.
    """
    try:
        stale_dir = path.parent / "stale"
        stale_dir.mkdir(parents=True, exist_ok=True)
        path.rename(stale_dir / path.name)
        return True
    except OSError as e:
        # Не тишина: файл останется «истиной на диске» и будет подхвачен.
        import logging

        logging.getLogger(__name__).warning("media_probe: перенос %s в stale/ не удался: %s", path, e)
        return False


async def probe_audio_silence(path: Path) -> float:
    """Аудио: не-тишина. Возвращает mean_volume (dBFS)."""
    if not path.is_file() or path.stat().st_size == 0:
        raise MediaProbeError("audio_unreadable", f"{path} отсутствует или пуст")
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-i",
        str(path),
        "-af",
        "volumedetect",
        "-f",
        "null",
        "-",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _stdout, stderr = await proc.communicate()
    err_text = (stderr or b"").decode(errors="ignore")
    if proc.returncode != 0:
        raise MediaProbeError("audio_unreadable", f"{path.name}: ffmpeg не читает ({err_text[:160]})")
    m = re.search(r"mean_volume:\s*(-?\d+(?:\.\d+)?)\s*dB", err_text)
    if not m:
        raise MediaProbeError("audio_unreadable", f"{path.name}: volumedetect без mean_volume")
    mean_db = float(m.group(1))
    if mean_db < SILENCE_MEAN_DB:
        raise MediaProbeError(
            "audio_silence",
            f"{path.name}: тишина (mean {mean_db:.1f} dB < {SILENCE_MEAN_DB} dB)",
        )
    return mean_db


async def probe_video_size(path: Path) -> tuple[int, int]:
    """Ширина и высота первого видеопотока (исходное разрешение файла)."""
    proc = await asyncio.create_subprocess_exec(
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height",
        "-of",
        "csv=p=0:s=x",
        str(path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe size failed for {path}: {stderr.decode(errors='ignore')}")
    raw = stdout.decode().strip().split("x")
    if len(raw) != 2:
        raise RuntimeError(f"ffprobe size parse failed for {path}: {stdout!r}")
    w, h = int(raw[0]), int(raw[1])
    if w <= 0 or h <= 0:
        raise RuntimeError(f"invalid video size {w}x{h} for {path}")
    return w, h


async def probe_duration(path: Path) -> float:
    """Реальная длительность: сначала видеопоток, иначе format."""
    # stream duration / nb_frames важнее format — иначе монтаж думает, что
    # клип короче/длиннее фактических кадров.
    proc = await asyncio.create_subprocess_exec(
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=duration,nb_frames,avg_frame_rate,r_frame_rate",
        "-of",
        "json",
        str(path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _stderr = await proc.communicate()
    if proc.returncode == 0:
        try:
            data = json.loads(stdout.decode() or "{}")
            streams = data.get("streams") or []
            if streams:
                st = streams[0]
                dur_s = st.get("duration")
                if dur_s not in (None, "N/A", ""):
                    return max(float(dur_s), 0.01)
                nb = st.get("nb_frames")
                rate = st.get("avg_frame_rate") or st.get("r_frame_rate") or "0/0"
                if nb not in (None, "N/A", "0", "") and "/" in str(rate):
                    num, den = str(rate).split("/", 1)
                    fps = float(num) / float(den) if float(den) else 0.0
                    if fps > 0:
                        return max(int(nb) / fps, 0.01)
        except (TypeError, ValueError, ZeroDivisionError, json.JSONDecodeError):
            pass

    proc2 = await asyncio.create_subprocess_exec(
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout2, stderr2 = await proc2.communicate()
    if proc2.returncode != 0:
        raise RuntimeError(f"ffprobe failed for {path}: {stderr2.decode(errors='ignore')}")
    return max(float(stdout2.decode().strip()), 0.01)
