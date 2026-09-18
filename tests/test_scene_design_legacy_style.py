"""Legacy style.json не роняет сборку KeyError LIST_KEY['style']."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from app.services.scene_design.runner import load_checkpoint


def test_load_checkpoint_legacy_style_does_not_keyerror(tmp_path: Path):
    """Старый style.json: сборка читает чекпоинт без KeyError."""
    project = SimpleNamespace(
        id=102,
        slug="style_legacy",
        data_dir=tmp_path,
        meta={
            "scene_design": {
                "agents": {"style": {"status": "done"}},
            }
        },
    )
    sd = tmp_path / "scene_design"
    sd.mkdir()
    (sd / "style.json").write_text(
        json.dumps({"style_arc": [{"тон": "тихо"}], "report": "ok"}, ensure_ascii=False),
        encoding="utf-8",
    )
    loaded = load_checkpoint(project, "style")
    assert loaded is not None
    assert loaded["style_arc"][0]["тон"] == "тихо"
