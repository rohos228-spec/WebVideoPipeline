"""Vision: oversized PNG сжимается, а не skip."""

from __future__ import annotations

import random
from pathlib import Path

from PIL import Image

from app.services.gpt_api import _MAX_VISION_BYTES, image_to_data_url


def test_image_to_data_url_compresses_large_png(tmp_path: Path) -> None:
    path = tmp_path / "huge.png"
    # Шум в КАЖДОМ пикселе: с шумом через один deflate ужимал файл до
    # 3.8 МБ (< лимита 4 МБ) в зависимости от версии zlib — тест падал
    # не по делу. Несжимаемый шум даёт ~12 МБ при любом кодеке.
    rng = random.Random(1)
    img = Image.frombytes("RGB", (2000, 2000), rng.randbytes(2000 * 2000 * 3))
    img.save(path, format="PNG")
    assert path.stat().st_size > _MAX_VISION_BYTES

    url = image_to_data_url(path)
    assert url.startswith("data:image/jpeg;base64,")
    # payload без префикса должен быть < лимита
    b64 = url.split(",", 1)[1]
    import base64

    raw = base64.b64decode(b64)
    assert len(raw) <= _MAX_VISION_BYTES
