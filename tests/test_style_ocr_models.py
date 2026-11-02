"""Выбор rec-модели RapidOCR: кириллица по умолчанию, без сети в тестах."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.services.style_agent import ocr, ocr_models


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("STYLE_OCR_REC_MODEL", raising=False)
    monkeypatch.setattr(ocr_models, "_cache_dir", lambda: tmp_path / "rapidocr")
    ocr._backend_cache.clear()


def test_default_downloads_eslav_once(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[Path] = []

    def fake_download(dest: Path) -> None:
        calls.append(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"onnx")

    monkeypatch.setattr(ocr_models, "_download", fake_download)
    first = ocr_models.rec_model_path()
    second = ocr_models.rec_model_path()
    assert first == second == tmp_path / "rapidocr" / ocr_models.ESLAV_NAME
    assert len(calls) == 1


def test_default_keyword_uses_bundled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STYLE_OCR_REC_MODEL", "default")
    assert ocr_models.rec_model_path() is None


def test_missing_custom_path_raises(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("STYLE_OCR_REC_MODEL", str(tmp_path / "nope.onnx"))
    with pytest.raises(ocr_models.RecModelUnavailable):
        ocr_models.rec_model_path()


def test_sha_mismatch_rejected(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    class _Resp:
        def __enter__(self) -> _Resp:
            return self

        def __exit__(self, *a: object) -> None:
            return None

        def raise_for_status(self) -> None:
            return None

        def iter_bytes(self, _n: int) -> list[bytes]:
            return [b"not the model"]

    import httpx

    monkeypatch.setattr(httpx, "stream", lambda *a, **k: _Resp())
    dest = tmp_path / "rapidocr" / ocr_models.ESLAV_NAME
    with pytest.raises(ocr_models.RecModelUnavailable, match="SHA256"):
        ocr_models._download(dest)
    assert not dest.exists()
    assert not dest.with_suffix(dest.suffix + ".part").exists()


def test_rapidocr_without_cyrillic_model_reports_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("rapidocr_onnxruntime")

    def boom(_dest: Path) -> None:
        raise ocr_models.RecModelUnavailable("не скачалась: ConnectError")

    monkeypatch.setattr(ocr_models, "_download", boom)
    assert ocr._rapidocr() is None
    assert "нет модели кириллицы" in ocr.backend_error()
    assert "ConnectError" in ocr.backend_error()


def test_build_lines_reads_split_row_left_to_right() -> None:
    # Детектор разрезал строку: правый кусок чуть выше левого
    raw = [
        {"t": t, "boxes": [["она крутит головой", 0.95, 0.861, 0.58], ["Поэтому", 0.98, 0.866, 0.31]]}
        for t in (0.0, 0.5, 1.0)
    ]
    lines = ocr.build_lines(raw, band=(0.8, 0.95))
    assert [ln.text for ln in lines] == ["Поэтому она крутит головой"]


def test_reading_order_keeps_two_subtitle_rows() -> None:
    boxes = [(0.90, 0.6, "вторая", 0.9), (0.80, 0.7, "первая", 0.9), (0.90, 0.3, "строка", 0.9)]
    assert [b[2] for b in ocr._reading_order(boxes)] == ["первая", "строка", "вторая"]
