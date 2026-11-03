"""Локальный fallback anim_pr без GPT."""

from __future__ import annotations

from types import SimpleNamespace

from app.services.animation_prompt_local import (
    build_local_ops_for_missing,
    compose_local_animation_prompt,
    project_aspect_slug,
)


def test_compose_local_has_no_voice_and_continuous_take() -> None:
    fr = SimpleNamespace(
        number=3,
        image_prompt=(
            "STYLE: Archival Noir. Improve only inside this style, never change medium. "
            "Background (detailed): серые панели переполненного вагона, жёлтая полоса у дверей. "
            "Action: пассажиры входят через открытые двери. "
            "Light: cold fluorescent. "
            "Negative: text, logos."
        ),
        voiceover_text="Утром 20 марта 1995 года токийское метро вошло в час пик.",
        meaning="",
        animation_prompt="",
        uuid="u-3",
    )
    text = compose_local_animation_prompt(fr)  # type: ignore[arg-type]
    assert "NO VOICE" in text
    assert "single continuous take" in text
    assert "--no voice" in text
    assert "вагон" in text.lower()
    assert "пассажир" in text.lower()


def test_build_ops_skips_ready() -> None:
    ready = SimpleNamespace(
        number=1,
        uuid="a",
        animation_prompt="x" * 40,
        image_prompt="scene",
        voiceover_text="",
        meaning="",
    )
    need = SimpleNamespace(
        number=2,
        uuid="b",
        animation_prompt="",
        image_prompt="STYLE: x. Сюжет: дверь вагона.",
        voiceover_text="дверь",
        meaning="",
    )
    ops = build_local_ops_for_missing([ready, need], shot=1)  # type: ignore[list-item]
    assert len(ops) == 1
    assert ops[0]["frame_uuid"] == "b"
    assert "промт_видео" in ops[0]["fields"]
    assert "aspect 9:16" in ops[0]["fields"]["промт_видео"]
    wide = build_local_ops_for_missing([need], shot=1, aspect="16:9")  # type: ignore[list-item]
    assert "aspect 16:9" in wide[0]["fields"]["промт_видео"]
    assert "9:16" not in wide[0]["fields"]["промт_видео"]


def _frame() -> SimpleNamespace:
    return SimpleNamespace(
        number=1,
        uuid="u",
        animation_prompt="",
        meaning="",
        image_prompt="STYLE: x. Сюжет: дверь вагона.",
        voiceover_text="дверь",
    )


def test_project_aspect_follows_project_then_canvas_node_then_default() -> None:
    proj = SimpleNamespace(aspect_ratio="16_9", meta={}, image_generator=None)
    assert project_aspect_slug(proj) == "16:9"
    assert "aspect 16:9" in compose_local_animation_prompt(  # type: ignore[arg-type]
        _frame(), aspect=project_aspect_slug(proj)
    )
    node = {"id": "v", "type": "videos", "data": {"aspectRatio": "1:1"}}
    proj.meta = {"canvas_graph": {"nodes": [node], "edges": []}}
    assert project_aspect_slug(proj) == "1:1"
    bare = SimpleNamespace(aspect_ratio=None, meta={}, image_generator=None)
    from app.services.vibecode_catalog import resolve_node_media_settings

    # Без формата в проекте - тот же Studio-дефолт, что у шагов картинок.
    assert project_aspect_slug(bare) == resolve_node_media_settings(bare, node_type="images")["aspect_slug"]
    assert project_aspect_slug(None) == "9:16"
