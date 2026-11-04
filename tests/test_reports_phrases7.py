"""Живой прогон фраз v7: «отчёт по всем проектам» — в чате строка на каждый проект отчёта.

Было: в чат шли 10 строк из 14, и ответ говорил «в данных были подробности о 10 из 14 проектов».
"""

from __future__ import annotations

from app.orchestrator.agents.question_context import fact_limit
from app.orchestrator.agents.summarizer import known_text
from app.services.action_layer import report_template as tpl
from tests.action_layer_support import env  # noqa: F401
from tests.test_action_layer_reports import RUNS, FakeCtx, _meta, _routes, _seed, prompts, specs  # noqa: F401


async def test_report_projects_lists_every_project_in_chat(env, specs, prompts) -> None:  # noqa: F811
    meta = _meta()
    first, slug = await _seed(env, title="Первый", meta=meta)
    ids = [first] + [
        (await _seed(env, title=f"Проект {n}", meta={}, frames=1, status="new"))[0] for n in range(13)
    ]
    ctx = FakeCtx(_routes(first, slug, meta, RUNS))
    out = await specs.get("report.projects").executor(ctx, {"project_ids": ids, "kind": "html"})
    text = known_text("report.projects", out)
    lines = [line for line in text.splitlines() if line.startswith("- #")]
    assert [int(line[3:].split()[0]) for line in lines] == ids
    assert "Ещё проектов" not in out["markdown"] and "Ещё проектов" not in text
    table = [row for row in out["markdown"].splitlines() if row.startswith("|")]
    assert len(table) == len(ids) + 2  # заголовок и разделитель
    # Итог задачи видит весь текст: 14 строк не обрезаются лимитом факта.
    assert len(text) <= fact_limit("report.projects")


def test_project_report_chat_rows_cover_the_report_limit() -> None:
    assert tpl.PROJECTS_CHAT_ROWS >= tpl.MAX_PROJECTS


async def test_member_report_projects_lists_every_project_in_chat(env, specs, prompts, monkeypatch) -> None:  # noqa: F811
    """Ветка участника (без файла и трат) — тоже строка на каждый проект, а не первые 10."""
    from app.services.action_layer import plan as plan_mod

    monkeypatch.setattr(plan_mod, "member_view", lambda: True)
    meta = _meta()
    first, slug = await _seed(env, title="Первый", meta=meta)
    ids = [first] + [
        (await _seed(env, title=f"Проект {n}", meta={}, frames=1, status="new"))[0] for n in range(13)
    ]
    ctx = FakeCtx(_routes(first, slug, meta, RUNS))
    out = await specs.get("report.projects").executor(ctx, {"project_ids": ids, "kind": "html"})
    assert out.get("file_format") is None  # участник: файл не пишется
    lines = [line for line in out["text_ru"].splitlines() if line.startswith("- #")]
    assert [int(line[3:].split()[0]) for line in lines] == ids
    assert "Ещё проектов" not in out["text_ru"]
    assert "$" not in out["text_ru"]
