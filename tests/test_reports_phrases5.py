"""Живой прогон фраз v5: отчёты.

#105 «какие поля перезаписала вторая работа с GPT» — ответ есть в отчёте по нодам (кто чьи поля перезаписал).
#123 «сравни проекты 5 и 7» — у каждого проекта своя строка: статус, шаги, кадры, картинки, видео, траты.
#124 «отчёт по кадрам с промтами» — готового отчёта группы «Кадры» нет: собирается отчёт по кадрам и нодам
со ссылками, а не «отчёта пока нет» без единой ссылки.
"""

from __future__ import annotations

pytest_plugins = ["tests.action_layer_support"]

import pytest

from app.models import Frame
from app.orchestrator.agents.summarizer import download_links, known_text
from app.services import prompt_library as pl
from app.services.action_layer import report_gpt as rg
from app.services.action_layer.builtin_group import register_group_actions
from app.services.action_layer.builtin_insight import register_insight
from app.services.action_layer.spec import ActionRegistry
from tests.test_action_layer_reports import REPLY, RUNS, FakeCtx, _meta, _routes, _seed, _write_reply


@pytest.fixture
def specs():
    reg = ActionRegistry()
    register_insight(reg)
    return reg


@pytest.fixture
def prompts(monkeypatch):
    monkeypatch.setattr(pl, "excel_gpt_prompt_exists", lambda name: name in ("razmetka", "geroi"))
    monkeypatch.setattr(
        pl,
        "load_file_meta",
        lambda step: {"razmetka": {"title": "Разметка кадров"}, "geroi": {"title": "Персонажи"}},
    )


SECOND = """```json
{"ops": [{"target": "frame", "frame_uuid": "uuid-1", "fields": {"image_prompt": "рыжий кот"}},
         {"target": "frame", "frame_uuid": "uuid-3", "fields": {"voiceover_text": "новый текст"}}]}
```"""


def test_overwrite_text_names_fields_taken_over_by_the_second_gpt_node() -> None:
    nodes = [
        {
            "label": "Работа с GPT 1",
            "node_key": "a",
            "frames": {"1": ["image_prompt", "place"], "2": ["image_prompt"]},
            "fields": ["image_prompt", "place"],
        },
        {
            "label": "Работа с GPT 2",
            "node_key": "b",
            "frames": {"1": ["image_prompt"], "2": ["image_prompt"], "3": ["voiceover_text"]},
            "fields": ["image_prompt", "voiceover_text"],
        },
        {"label": "Работа с GPT 3", "node_key": "c", "frames": {}, "fields": []},
    ]
    text = rg.overwrite_text(nodes)
    lines = text.splitlines()
    assert "Работа с GPT 1" in lines[1] and "чужих полей не перезаписала" in lines[1]
    assert lines[2] == (
        "- Работа с GPT 2 перезаписала поля, записанные «Работа с GPT 1»: промт картинки (кадров 2)."
    )
    assert "Работа с GPT 3: пока ничего не записала" in lines[3]


def test_overwrite_text_same_label_nodes_still_overwrite() -> None:
    """Две ноды с одним названием — разные ноды: перезапись видна, как в колонке html."""
    nodes = [
        {
            "label": "Работа с GPT",
            "node_key": "a",
            "frames": {"1": ["image_prompt"]},
            "fields": ["image_prompt"],
        },
        {
            "label": "Работа с GPT",
            "node_key": "b",
            "frames": {"1": ["image_prompt"]},
            "fields": ["image_prompt"],
        },
    ]
    lines = rg.overwrite_text(nodes).splitlines()
    assert lines[2] == (
        "- Работа с GPT перезаписала поля, записанные «Работа с GPT» (другая нода с тем же "
        "названием): промт картинки (кадров 1)."
    )


async def test_report_nodes_text_says_what_the_second_gpt_node_overwrote(env, specs, prompts) -> None:
    meta = _meta()
    pid, slug = await _seed(env, meta=meta)
    _write_reply(slug, "excel_gpt_1", REPLY)
    _write_reply(slug, "excel_gpt_2", SECOND)
    ctx = FakeCtx(_routes(pid, slug, meta, RUNS))
    chat = await specs.get("report.nodes").executor(ctx, {"project_id": pid})
    # Вопрос в чате читает markdown отчёта: ответ о перезаписи в нём, а не «не указано».
    assert (
        "Работа с GPT 2 перезаписала поля, записанные «Работа с GPT 1»: промт картинки (кадров 1)"
        in chat["markdown"]
    )
    # Итог задачи читает text_ru: ответ о перезаписи и там.
    assert "Работа с GPT 2 перезаписала поля, записанные «Работа с GPT 1»" in chat["text_ru"]


async def test_report_projects_text_has_a_line_per_project_for_comparison(env, specs, prompts) -> None:
    meta = _meta()
    a, slug_a = await _seed(env, title="Первый", meta=meta, frames=3, images=2, videos=1, usd=0.5)
    b, _ = await _seed(env, title="Второй", meta={}, frames=1, status="new")
    ctx = FakeCtx(_routes(a, slug_a, meta, RUNS))
    out = await specs.get("report.projects").executor(ctx, {"project_ids": [a, b], "kind": "html"})
    text = known_text("report.projects", out)
    line_a = next(line for line in text.splitlines() if line.startswith(f"- #{a} "))
    line_b = next(line for line in text.splitlines() if line.startswith(f"- #{b} "))
    assert "«Ролик про кота»" in line_a and "кадров 3, картинок 2, видео 1, траты $0.50 (оценка)" in line_a
    assert "шагов готово" in line_a and "Не указано" not in text
    assert "«Второй»" in line_b and "кадров 1, картинок 0, видео 0, траты: нет данных" in line_b
    assert len(download_links(out)) == 2


async def test_report_projects_lines_for_member_have_no_money(env, specs, prompts, monkeypatch) -> None:
    """Участник студии: строки по проектам есть, сумм в $ в них нет (их видит только админ)."""
    from app.services.action_layer import plan as plan_mod

    monkeypatch.setattr(plan_mod, "member_view", lambda: True)
    meta = _meta()
    a, slug_a = await _seed(env, title="Первый", meta=meta, frames=3, images=2, videos=1, usd=0.5)
    b, _ = await _seed(env, title="Второй", meta={}, frames=1, status="new")
    ctx = FakeCtx(_routes(a, slug_a, meta, RUNS))
    out = await specs.get("report.projects").executor(ctx, {"project_ids": [a, b], "kind": "html"})
    text = out["text_ru"]
    line_a = next(line for line in text.splitlines() if line.startswith(f"- #{a} "))
    line_b = next(line for line in text.splitlines() if line.startswith(f"- #{b} "))
    assert line_a.endswith("кадров 3, картинок 2, видео 1.") and "«Второй»" in line_b
    assert "$" not in text and "траты" not in text


async def test_shots_report_without_group_report_builds_frames_report_with_links(env, prompts) -> None:
    reg = ActionRegistry()
    register_insight(reg)
    register_group_actions(reg)
    meta = _meta()
    pid, slug = await _seed(env, meta=meta)
    async with env.factory() as s:
        frame = (
            await s.execute(Frame.__table__.select().where(Frame.project_id == pid, Frame.number == 1))
        ).first()
        await s.execute(
            Frame.__table__.update().where(Frame.id == frame.id).values(image_prompt="кот на кухне")
        )
        await s.commit()
    ctx = FakeCtx(_routes(pid, slug, meta, RUNS))  # /shots-report нет → 404
    out = await reg.get("group.shots_report").executor(ctx, {"project_id": pid})
    text = known_text("group.shots_report", out)
    assert out["exists"] is False and out["fallback"] == "report.nodes"
    assert "кадров 3, с промтом картинки 1" in text
    assert len(download_links(out)) == 2 and "[Скачать отчёт (html)]" in text
    page = (await env.client.get(out["download_url"])).text
    assert "Промт картинки." in page and "кот на кухне" in page
