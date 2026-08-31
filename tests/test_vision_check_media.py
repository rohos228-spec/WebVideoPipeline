"""Картинки для vision уходят лентами, а не сырыми 2K PNG.

Живой прогон 2026-08-31: проверка кадров не запускалась вовсе — и 33 кадра,
и даже 8 давали HTTP 413. Лента 768 px решает вес и путаницу панелей; тот же
приём уже применяется в animation_prompt_gpt и video_sheet.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.services.vision_check_media import PACK_FROM, PANELS_PER_STRIP, pack_images_for_vision


def _png(path: Path, size: tuple[int, int] = (200, 320)) -> Path:
    from PIL import Image

    Image.new("RGB", size, (140, 90, 200)).save(path)
    return path


def test_few_images_pass_through(tmp_path: Path) -> None:
    """Одну-две картинки паковать незачем — в полном разрешении виднее."""
    paths = [_png(tmp_path / f"frame_{i}.png") for i in range(PACK_FROM - 1)]
    assert pack_images_for_vision(paths, tmp_path / "out") == paths


def test_many_images_packed_into_strips(tmp_path: Path) -> None:
    paths = [_png(tmp_path / f"frame_{i:03d}.png") for i in range(PANELS_PER_STRIP * 2 + 1)]
    packed = pack_images_for_vision(paths, tmp_path / "out")
    assert len(packed) == 3  # 6 + 6 + 1
    assert all(p.exists() for p in packed)
    assert all(p.parent.name == "out" for p in packed)


def test_strip_is_much_lighter_than_originals(tmp_path: Path) -> None:
    """Смысл упаковки — вес: лента должна быть меньше суммы исходников."""
    paths = [_png(tmp_path / f"frame_{i:03d}.png", (1152, 2048)) for i in range(6)]
    packed = pack_images_for_vision(paths, tmp_path / "out")
    assert len(packed) == 1
    assert packed[0].stat().st_size < sum(p.stat().st_size for p in paths)


def test_non_images_are_kept(tmp_path: Path) -> None:
    """Рядом с картинками ездят json/txt — их терять нельзя."""
    data = tmp_path / "db_frames.json"
    data.write_text("{}", encoding="utf-8")
    paths = [data, *[_png(tmp_path / f"frame_{i}.png") for i in range(PACK_FROM + 1)]]
    packed = pack_images_for_vision(paths, tmp_path / "out")
    assert data in packed
    assert len(packed) == 2  # json + одна лента


def test_broken_strip_falls_back_to_originals(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Склейка упала — работаем на исходниках, а не теряем вход."""
    paths = [_png(tmp_path / f"frame_{i}.png") for i in range(PACK_FROM + 1)]

    def boom(*_args, **_kwargs):
        raise RuntimeError("PIL сломался")

    monkeypatch.setattr("app.services.image_strip.compose_horizontal_strip", boom)
    assert pack_images_for_vision(paths, tmp_path / "out") == paths


def test_missing_files_ignored(tmp_path: Path) -> None:
    """Путь есть, файла нет — не падаем."""
    real = [_png(tmp_path / f"frame_{i}.png") for i in range(PACK_FROM + 1)]
    ghost = tmp_path / "frame_ghost.png"
    packed = pack_images_for_vision([*real, ghost], tmp_path / "out")
    assert ghost in packed
