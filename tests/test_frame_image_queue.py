"""Очередь generate_images: диск важнее статуса в БД."""

from __future__ import annotations

from pathlib import Path

from app.models import Frame, FrameStatus
from app.services.scan_frames import frame_needs_shot1_image, is_valid_scene_image


def _frame(n: int, status: FrameStatus, prompt: str = "p") -> Frame:
    fr = Frame(project_id=1, number=n, voiceover_text="v")
    fr.status = status
    fr.image_prompt = prompt
    return fr


def test_image_generated_without_file_still_needs_outsee(tmp_path: Path) -> None:
    scenes = tmp_path / "scenes"
    scenes.mkdir()
    fr = _frame(3, FrameStatus.image_generated)
    assert frame_needs_shot1_image(fr, scenes) is True


def _png(path: Path, size: tuple[int, int]) -> Path:
    """Настоящий PNG заданного размера — шум, чтобы файл не ужался в ничто."""
    import os

    from PIL import Image

    img = Image.frombytes("RGB", size, os.urandom(size[0] * size[1] * 3))
    img.save(path)
    return path


def test_valid_png_on_disk_skips_generation(tmp_path: Path) -> None:
    scenes = tmp_path / "scenes"
    scenes.mkdir()
    png = _png(scenes / "frame_003_abcd1234.png", (720, 1280))
    assert is_valid_scene_image(png)
    fr = _frame(3, FrameStatus.image_prompt_ready)
    assert frame_needs_shot1_image(fr, scenes) is False


def test_dark_720p_frame_is_valid_even_if_light(tmp_path: Path) -> None:
    """Тёмный кадр весит мало — это не повод считать его невалидным.

    Регрессия живого прогона: порог «валидности» стоял в 200 КБ, а MiniMax
    при 720×1280 отдаёт 170–240 КБ. Тёмный кадр не дотягивал, сканер ставил
    его обратно в очередь — и кадр перегенерировался бесконечно, за деньги.
    """
    from PIL import Image

    scenes = tmp_path / "scenes"
    scenes.mkdir()
    png = scenes / "frame_003_abcd1234.png"
    Image.new("RGB", (720, 1280), (6, 10, 14)).save(png)
    assert png.stat().st_size < 200_000
    assert is_valid_scene_image(png)
    fr = _frame(3, FrameStatus.image_generated)
    assert frame_needs_shot1_image(fr, scenes) is False


def test_thumbnail_still_needs_generation(tmp_path: Path) -> None:
    """Превью 128×128 — не кадр, сколько бы оно ни весило."""
    scenes = tmp_path / "scenes"
    scenes.mkdir()
    png = _png(scenes / "frame_003_abcd1234.png", (128, 128))
    assert is_valid_scene_image(png) is False
    fr = _frame(3, FrameStatus.image_generated)
    assert frame_needs_shot1_image(fr, scenes) is True


def test_truncated_file_still_needs_generation(tmp_path: Path) -> None:
    """Обрывок с правильным magic — не картинка."""
    scenes = tmp_path / "scenes"
    scenes.mkdir()
    png = scenes / "frame_003_abcd1234.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 50_000)
    assert is_valid_scene_image(png) is False
    fr = _frame(3, FrameStatus.image_generated)
    assert frame_needs_shot1_image(fr, scenes) is True
