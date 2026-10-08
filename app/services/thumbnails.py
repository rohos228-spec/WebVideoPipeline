"""Миниатюры изображений для сетки истории генераций.

Сетка тянет полные файлы (2K PNG по мегабайты) десятками за раз. Тамбнейл
320px WebP весит десятки килобайт и генерируется один раз: ключ кэша —
sha1(путь + mtime), смена файла даёт новый ключ (старые чистятся лениво
по TTL, см. `prune_thumbs`). Безопасность не здесь: вызывающий обязан
сначала прогнать `file_scope.assert_readable` — сюда приходит только уже
разрешённый путь.
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path

THUMB_WIDTH = 320
THUMB_QUALITY = 80
THUMB_TTL_S = 30 * 24 * 3600

_IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp"})


def is_thumbable(path: Path) -> bool:
    return path.suffix.lower() in _IMAGE_SUFFIXES


def thumbs_dir() -> Path:
    from app.settings import settings

    root = Path(settings.data_dir) / ".thumbs"
    root.mkdir(parents=True, exist_ok=True)
    return root


def thumbnail_path(source: Path) -> Path | None:
    """Путь кэшированного тамбнейла либо None (не картинка / PIL упал)."""
    if not is_thumbable(source):
        return None
    try:
        st = source.stat()
    except OSError:
        return None
    key = hashlib.sha1(f"{source.resolve()}@{st.st_mtime_ns}".encode()).hexdigest()
    out = thumbs_dir() / f"{key}.webp"
    if out.is_file():
        return out
    try:
        from PIL import Image

        with Image.open(source) as img:
            img = img.convert("RGB")
            w, h = img.size
            if w > THUMB_WIDTH:
                img = img.resize((THUMB_WIDTH, max(1, round(h * THUMB_WIDTH / w))))
            img.save(out, format="WEBP", quality=THUMB_QUALITY, method=6)
    except Exception:  # noqa: BLE001 — битый файл: отдадим оригинал
        return None
    return out if out.is_file() else None


def prune_thumbs() -> int:
    """Снести протухшие тамбнейлы. Возвращает число удалённых."""
    now = time.time()
    removed = 0
    try:
        root = thumbs_dir()
    except OSError:
        return 0
    for fp in root.glob("*.webp"):
        try:
            if now - fp.stat().st_mtime > THUMB_TTL_S:
                fp.unlink(missing_ok=True)
                removed += 1
        except OSError:  # noqa: BLE001, PERF203
            continue
    return removed
