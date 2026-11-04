"""Модель распознавания кириллицы для rapidocr.

Встроенная в ``rapidocr_onnxruntime`` модель китайско-английская: русский
субтитр она превращает в «OHa OTBena B3rnAA». Поэтому берём официальную
``eslav_PP-OCRv5_rec_mobile`` (русский, украинский, белорусский, плюс латиница
и цифры; словарь символов вшит в onnx) из хаба RapidOCR на ModelScope.
Файл ~8 МБ, качается один раз в ``DATA_DIR/.cache/rapidocr`` со сверкой SHA256.

``STYLE_OCR_REC_MODEL``: путь к своей rec-модели или ``default`` (встроенная,
для англоязычных роликов). Пусто → eslav.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from loguru import logger

ESLAV_NAME = "eslav_PP-OCRv5_rec_mobile.onnx"
ESLAV_URL = (
    "https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/v3.10.0/"
    "onnx/PP-OCRv5/rec/eslav_PP-OCRv5_rec_mobile.onnx"
)
ESLAV_SHA256 = "08705d6721849b1347d26187f15a5e362c431963a2a62bfff4feac578c489aab"
_DOWNLOAD_TIMEOUT_S = 300.0


class RecModelUnavailable(RuntimeError):
    """Кириллическую модель не удалось получить: OCR русских субтитров невозможен."""


def _cache_dir() -> Path:
    try:
        from app.settings import settings

        base = Path(settings.data_dir)
    except Exception:  # noqa: BLE001
        base = Path(os.environ.get("DATA_DIR") or "./data")
    return base / ".cache" / "rapidocr"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _download(dest: Path) -> None:
    import httpx

    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")
    logger.info("rapidocr: качаю кириллическую модель {} (~8 МБ)", ESLAV_NAME)
    try:
        with httpx.stream("GET", ESLAV_URL, follow_redirects=True, timeout=_DOWNLOAD_TIMEOUT_S) as r:
            r.raise_for_status()
            with part.open("wb") as f:
                for chunk in r.iter_bytes(1 << 20):
                    f.write(chunk)
        got = _sha256(part)
        if got != ESLAV_SHA256:
            raise RecModelUnavailable(f"SHA256 не совпал ({got[:12]}…), файл не принят")
        part.replace(dest)
    except RecModelUnavailable:
        part.unlink(missing_ok=True)
        raise
    except Exception as exc:  # noqa: BLE001
        part.unlink(missing_ok=True)
        raise RecModelUnavailable(f"не скачалась: {type(exc).__name__}: {exc}") from exc


def rec_model_path() -> Path | None:
    """Путь к rec-модели для RapidOCR; None — встроенная (только по явному ``default``).

    Raises:
        RecModelUnavailable: eslav нет на диске и скачать не вышло.
    """
    raw = (os.environ.get("STYLE_OCR_REC_MODEL") or "").strip()
    if raw.lower() == "default":
        return None
    if raw:
        p = Path(raw)
        if not p.is_file():
            raise RecModelUnavailable(f"STYLE_OCR_REC_MODEL: файла нет ({p})")
        return p
    dest = _cache_dir() / ESLAV_NAME
    if not dest.is_file():
        _download(dest)
    return dest
