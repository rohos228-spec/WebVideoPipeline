"""Vision-проверка кадров: картинки уезжают лентами, и строго после снапшота.

Живой прогон 2026-08-31 (п.21): сырые PNG полного разрешения не влезали в
запрос вовсе — и 33 кадра, и 8 давали HTTP 413, то есть контур проверки кадров
на ролике штатной длины не запускался. Тест держит сам вызов в `run`: ленты
собираются только для scenes/hero, подменяют список файлов и не трогают
снапшот «Базы» (он построен ДО ленты, пофреймово по именам).
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.models import ProjectStatus
from app.orchestrator.steps import enrich_xlsx as ex


class _StopHere(RuntimeError):
    """Сентинел: дальше vision-подготовки идти незачем."""


@pytest.mark.asyncio
async def test_scenes_check_packs_images_into_strips(tmp_path) -> None:
    img = tmp_path / "frame_001.png"
    img.write_bytes(b"png")
    strip = tmp_path / "strip_01.png"

    project = SimpleNamespace(
        id=62,
        slug="p62",
        status=ProjectStatus.enriching_1,
        meta={"active_excel_gpt_node_key": "n_check"},
        data_dir=tmp_path,
    )
    resolved = {
        "canRun": True,
        "checkMode": True,
        "role": "review",
        "outputMode": "text",
        "checkPromptSource": "upstream",
        "files": [{"ok": True, "name": img.name, "path": str(img)}],
    }
    sheet = SimpleNamespace(ensure_initialized=lambda **_k: tmp_path / "book.xlsx")
    seen: dict = {}

    async def _preflight(project, paths, kind):
        return list(paths), []

    async def _snapshot(session, project, *, image_paths, kind):
        seen["snapshot_paths"] = [Path(p).name for p in image_paths]
        return "SNAP"

    def _pack(paths, out_dir):
        seen["packed_from"] = [Path(p).name for p in paths]
        return [strip]

    def _cancel(_pid):
        seen["paths_at_call"] = None  # заполняется ниже — сюда доехали
        raise _StopHere

    with (
        patch.object(ex, "_sheet_for_project", return_value=sheet),
        patch.object(ex, "_get_accompanying_text", return_value=""),
        patch.object(
            ex,
            "read_resolved_project_prompt",
            return_value=("v", None, "мастер", "custom"),
        ),
        patch("app.services.gpt_operator.operator_config", return_value={}),
        patch("app.services.gpt_operator.resolve_operator", return_value=resolved),
        patch("app.services.gpt_operator.sanitize_check_reviewer_notes", return_value=""),
        patch("app.services.gpt_operator.resolve_check_report_format", return_value=("md", None)),
        patch(
            "app.services.gpt_operator.collect_source_prompts",
            return_value=[{"ok": True, "nodeKey": "n_src", "text": "исходный"}],
        ),
        patch("app.services.gpt_operator.assemble_check_master_prompt", return_value="MASTER"),
        patch("app.services.gpt_operator.append_vision_hint_for_upstream", side_effect=lambda p, n, m: m),
        patch("app.services.gpt_operator.project_format_hint_for_check", return_value=""),
        patch("app.services.gpt_operator.upstream_node_type_for_check", return_value="images"),
        patch("app.services.vision_check_loop.get_vision_kind", return_value=""),
        patch(
            "app.services.vision_check_loop.filter_image_paths_for_recheck",
            side_effect=lambda p, paths: paths,
        ),
        patch("app.services.vision_check_loop.preflight_media_for_check", side_effect=_preflight),
        patch("app.services.vision_check_db.build_vision_db_snapshot", side_effect=_snapshot),
        patch("app.services.vision_check_media.pack_images_for_vision", side_effect=_pack),
        patch("app.services.step_cancel.raise_if_cancelled", side_effect=_cancel),
    ):
        with pytest.raises(_StopHere):
            await ex.run(AsyncMock(), project, AsyncMock())

    # Снапшот построен по исходным кадрам, лента — уже после него.
    assert seen["snapshot_paths"] == [img.name]
    assert seen["packed_from"] == [img.name]
    assert "paths_at_call" in seen, "до vision-подготовки не доехали"
