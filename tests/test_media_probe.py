"""Этап 4 (C): media_probe — дешёвые пре-чеки до платного vision."""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import settings as app_settings
from app.models import Project, ProjectStatus
from app.services import vision_check_loop as vcl
from app.services.media_probe import (
    MediaProbeError,
    _parse_aspect,
    probe_image,
    probe_video,
    stash_rejected_file,
)

_HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def _png(path: Path, size=(90, 160), color=(120, 90, 60)) -> Path:
    from PIL import Image

    img = Image.new("RGB", size, color)
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path)
    return path


def test_parse_aspect() -> None:
    assert _parse_aspect("9:16") == pytest.approx(9 / 16)
    assert _parse_aspect("16x9") == pytest.approx(16 / 9)
    assert _parse_aspect("") is None
    assert _parse_aspect("weird") is None


def test_probe_image_ok_and_black(tmp_path: Path) -> None:
    good = _png(tmp_path / "good.png")
    w, h = asyncio.run(probe_image(good))
    assert (w, h) == (90, 160)

    black = _png(tmp_path / "black.png", color=(0, 0, 0))
    with pytest.raises(MediaProbeError) as ei:
        asyncio.run(probe_image(black))
    assert ei.value.code == "black_image"
    assert ei.value.reason == "media_probe:black_image"


def test_probe_image_dark_scene_not_black(tmp_path: Path) -> None:
    """Порог почти-абсолютный: тёмная сцена со светами проходит."""
    from PIL import Image

    img = Image.new("RGB", (90, 160), (10, 10, 12))
    for x in range(20):
        for y in range(20):
            img.putpixel((x, y), (220, 210, 180))  # окно/фонарь
    p = tmp_path / "night.png"
    img.save(p)
    asyncio.run(probe_image(p))  # не бросает


def test_probe_image_aspect_mismatch(tmp_path: Path) -> None:
    square = _png(tmp_path / "sq.png", size=(100, 100))
    with pytest.raises(MediaProbeError) as ei:
        asyncio.run(probe_image(square, expect_aspect="9:16"))
    assert ei.value.code == "aspect_mismatch"
    # без ожидания — проходит
    asyncio.run(probe_image(square))


def test_probe_image_unreadable(tmp_path: Path) -> None:
    bad = tmp_path / "junk.png"
    bad.write_bytes(b"not a png at all")
    with pytest.raises(MediaProbeError) as ei:
        asyncio.run(probe_image(bad))
    assert ei.value.code == "image_unreadable"
    missing = tmp_path / "missing.png"
    with pytest.raises(MediaProbeError):
        asyncio.run(probe_image(missing))


@pytest.mark.skipif(not _HAS_FFMPEG, reason="ffmpeg/ffprobe недоступны")
def test_probe_video_unreadable(tmp_path: Path) -> None:
    bad = tmp_path / "junk.mp4"
    bad.write_bytes(b"garbage" * 100)
    with pytest.raises(MediaProbeError) as ei:
        asyncio.run(probe_video(bad))
    assert ei.value.code == "video_unreadable"


@pytest.mark.skipif(not _HAS_FFMPEG, reason="ffmpeg/ffprobe недоступны")
def test_video_sheet_raises_on_bad_clip_not_8s(tmp_path: Path) -> None:
    """Этап 4 (C.2, §9#10): битый клип — исключение, не молчаливые 8.0 c."""
    from app.services.video_sheet import build_video_sheet

    bad = tmp_path / "clip_003_junk.mp4"
    bad.write_bytes(b"garbage" * 100)
    with pytest.raises(Exception):
        asyncio.run(build_video_sheet(bad, tmp_path / "sheets", frame_number=3))


def test_stash_rejected_file(tmp_path: Path) -> None:
    """Этап 4 (C.4, блокер панели): отбракованный файл уходит в stale/."""
    f = tmp_path / "scenes" / "frame_003_x.png"
    _png(f)
    assert stash_rejected_file(f) is True
    assert not f.exists()
    assert (tmp_path / "scenes" / "stale" / "frame_003_x.png").exists()


def _project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, slug: str) -> Project:
    monkeypatch.setattr(app_settings.settings, "data_dir", tmp_path / "data")
    p = Project(
        slug=slug,
        topic="t",
        status=ProjectStatus.enrich_1_ready,
        hero_mode="no_hero",
        meta={},
        aspect_ratio="9:16",
    )
    p.data_dir.mkdir(parents=True, exist_ok=True)
    return p


def test_preflight_filters_and_records_targets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Этап 4 (C.3): брак исключён из vision-входа, цель в meta с причиной."""
    p = _project(tmp_path, monkeypatch, "pf")
    good = _png(tmp_path / "frame_001_a.png")
    black = _png(tmp_path / "frame_003_b.png", color=(0, 0, 0))
    txt = tmp_path / "db_check.json"
    txt.write_text("{}", encoding="utf-8")

    ok_paths, bad = asyncio.run(vcl.preflight_media_for_check(p, [good, black, txt], "scenes"))
    assert good in ok_paths and txt in ok_paths
    assert black not in ok_paths
    assert bad == [{"token": "f3", "reason": "media_probe:black_image"}]
    assert p.meta["media_probe_regen"] == bad

    frame_tgts, hero_ids, reasons = vcl.probe_targets_from_meta(p, "scenes")
    assert frame_tgts == [{"number": 3, "shot": 1}]
    assert hero_ids == []
    assert "media_probe:black_image" in reasons[0]


def test_probe_targets_hero_kind() -> None:
    p = Project(
        slug="x",
        topic="t",
        status=ProjectStatus.enrich_1_ready,
        meta={
            "media_probe_regen": [
                {"token": "c01", "reason": "media_probe:image_unreadable"},
                {"token": "f5", "reason": "media_probe:black_image"},
            ]
        },
    )
    frame_tgts, hero_ids, _ = vcl.probe_targets_from_meta(p, "hero")
    assert frame_tgts == []
    assert hero_ids == ["c01"]


def test_probe_targets_force_fail_and_merge(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Этап 4 (C.3): probe-цели перебивают pass-гейт и вливаются в regen."""
    from app.services.excel_gpt_node import upload_dir

    p = _project(tmp_path, monkeypatch, "pfm")
    check_key = "n_check_img"
    p.meta = {
        "excel_gpt_nodes": {
            check_key: {"checkMode": True, "checkFix": False, "slotIndex": 2},
        },
        "gpt_operator_results": {check_key: {"gateStatus": "pass"}},
        "media_probe_regen": [{"token": "f4", "reason": "media_probe:video_unreadable"}],
        "canvas_graph": {
            "nodes": [
                {"id": "n_img", "type": "images"},
                {
                    "id": check_key,
                    "type": "excel_gpt",
                    "data": {"slotIndex": 2, "checkMode": True},
                },
            ],
            "edges": [{"source": "n_img", "target": check_key, "data": {"kind": "after"}}],
        },
    }
    out = upload_dir(p, check_key)
    out.mkdir(parents=True, exist_ok=True)
    (out / "gpt_reply_raw.txt").write_text(
        "# ОТЧЁТ ПРОВЕРКИ\nverdict: pass\n\n## scores\noverall: 0.9\n\n"
        "## issues\n- [ok] frame_001_a.png: ок\n",
        encoding="utf-8",
    )

    async def _fake_prepare(*_a, **_k):
        return True

    monkeypatch.setattr("app.services.run_sync.prepare_node_for_step_start", _fake_prepare)

    class _Sess:
        async def flush(self):
            return None

        async def execute(self, *_a, **_k):
            class _R:
                def scalars(self):
                    return SimpleNamespace(all=lambda: [])

            return _R()

        async def delete(self, *_a, **_k):
            return None

    started = asyncio.run(vcl.maybe_start_vision_check_loop_after_check(_Sess(), p, check_key))
    assert started is True
    assert p.status is ProjectStatus.generating_images
    assert vcl.get_scene_check_regen(p) == [{"number": 4, "shot": 1}]
    # probe-цели потреблены
    assert p.meta.get("media_probe_regen") == []


def test_synthetic_probe_fail_result(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Этап 4 (C.3): «все файлы битые» — fail-отчёт без vision-вызова."""
    from app.orchestrator.steps.enrich_xlsx import _synthetic_probe_fail_result
    from app.services.excel_gpt_node import upload_dir

    p = _project(tmp_path, monkeypatch, "syn")
    res = _synthetic_probe_fail_result(
        p,
        "n_check",
        [
            {"token": "f3", "reason": "media_probe:black_image"},
            {"token": "f7s2", "reason": "media_probe:video_unreadable"},
        ],
    )
    assert res.gate_status == "fail"
    raw = (upload_dir(p, "n_check") / "gpt_reply_raw.txt").read_text(encoding="utf-8")
    assert "[critical] f3" in raw
    assert "media_probe:video_unreadable" in raw
    from app.services.check_analysis import extract_critical_frame_regen_targets

    tgts = extract_critical_frame_regen_targets(raw)
    assert {"number": 3, "shot": 1} in tgts
    assert {"number": 7, "shot": 2} in tgts
