"""Ответы брифинга доходят до промтов шагов: сценарий, разбивка, картинки, анимация."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from app.services import chatgpt_xlsx as cx
from app.services.node_step_params import (
    build_briefing_media_block,
    build_split_params_block,
    build_step_params_block,
)

BRIEF = {
    "scenario_type": "Вирусный динамичный Shorts",
    "visual_style": "3D мультфильм / CGI",
    "animation_style": "плавная камера, мягкие наезды",
    "transitions": "склейки 2–3с",
    "montage": "клиповый с субтитрами",
}


def _project(meta: dict) -> SimpleNamespace:
    return SimpleNamespace(id=1, topic="Совы", meta=meta, gpt_text_overrides={}, prompt_variants={})


def test_plan_and_split_get_scenario_and_montage() -> None:
    p = _project({"briefing": dict(BRIEF)})
    assert "Тип сценария: Вирусный динамичный Shorts" in build_step_params_block(p, "plan")
    assert "Монтаж: клиповый с субтитрами" in build_split_params_block(p)


def test_anim_pr_block_has_animation_style() -> None:
    block = build_briefing_media_block(_project({"briefing": dict(BRIEF)}), "anim_pr")
    assert "Стиль анимации: плавная камера, мягкие наезды" in block
    assert "Монтаж: клиповый с субтитрами" in block


def test_img_pr_style_from_briefing_only_without_own_style() -> None:
    p = _project({"briefing": dict(BRIEF)})
    assert "Визуальный стиль: 3D мультфильм / CGI" in build_briefing_media_block(p, "img_pr")
    own = _project({"briefing": dict(BRIEF), "img_pr_style_id": "night_paper"})
    assert build_briefing_media_block(own, "img_pr") == ""


def test_no_briefing_no_block() -> None:
    assert build_briefing_media_block(_project({}), "anim_pr") == ""


def test_anim_pr_prompt_file_contains_briefing(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(cx, "_get_master_or_fallback", lambda project, step, fallback: "# мастер")
    p = _project({"briefing": dict(BRIEF)})
    text = cx.write_anim_pr_prompt_file(p, tmp_path, ts="t").read_text(encoding="utf-8")
    assert text.startswith("# мастер")
    assert "Стиль анимации: плавная камера, мягкие наезды" in text
    img = cx.write_img_pr_prompt_file(p, tmp_path, ts="t").read_text(encoding="utf-8")
    assert "Визуальный стиль: 3D мультфильм / CGI" in img
