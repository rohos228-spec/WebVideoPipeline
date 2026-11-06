"""«Выгрузи переписку за неделю»: вся переписка чата одним файлом вместо скриншотов."""

from __future__ import annotations

import json

import pytest

from app.services.action_layer import chat_export as ce


@pytest.mark.parametrize(
    ("text", "days"),
    [
        ("выгрузи переписку за неделю", 7),
        ("скинь всю переписку чата за сегодня", 1),
        ("сохрани историю чата за месяц", 30),
        ("выгрузи переписку за 3 дня", 3),
        ("дай диалоги за две недели", 14),
        ("переписка чата одним файлом", 7),
    ],
)
def test_export_phrases(text: str, days: int) -> None:
    from app.orchestrator.agents.question_context import _norm

    assert ce.export_params(_norm(text)) == {"days": days}


@pytest.mark.parametrize("text", ["что с проектом", "покажи промт кадра 3", "выгрузи данные gpt"])
def test_not_export(text: str) -> None:
    from app.orchestrator.agents.question_context import _norm

    assert ce.export_params(_norm(text)) is None


def test_plan_reads_routes_to_chat_export() -> None:
    from app.orchestrator.agents.question_context import plan_reads

    assert plan_reads("выгрузи переписку за неделю", 26) == [("chat.export", {"days": 7})]
    assert plan_reads("скинь переписку этого проекта за сегодня", 26) == [
        ("chat.export", {"days": 1, "project_id": 26})
    ]
    assert plan_reads("выгрузи переписку по проекту 12 за месяц", None) == [
        ("chat.export", {"days": 30, "project_id": 12})
    ]


def test_render_md_groups_by_project_with_task_detail() -> None:
    rows = [
        {
            "id": 1, "session_id": "studio-p26", "task_id": 5, "role": "user", "mode": "task",
            "content": "запусти картинки", "created_at": "2026-11-06T10:00:00", "project_id": 26,
            "task_status": "done", "task_error": None,
            "plan_json": json.dumps({"steps": [{"action": "step.run_step", "title_ru": "Запустить картинки"}]}),
        },
        {
            "id": 2, "session_id": "studio-p26", "task_id": 5, "role": "assistant", "mode": "task",
            "content": "Готово.", "created_at": "2026-11-06T10:00:30", "project_id": 26,
            "task_status": "done", "task_error": None, "plan_json": None,
        },
        {
            "id": 3, "session_id": "studio-p46", "task_id": None, "role": "assistant", "mode": "legacy",
            "content": "Ответ старого окна", "created_at": "2026-11-06T11:00:00", "project_id": 46,
            "task_status": None, "task_error": None, "plan_json": None,
        },
    ]
    md = ce.render_md(rows, 7, None)
    assert "## Проект 26" in md and "## Проект 46" in md
    assert "задача #5: done; план: Запустить картинки" in md
    assert "Реплик человека: 1, ответов чата: 2, проектов: 2." in md
    assert "(старый путь окна)" in md
    assert "Реплик за период нет." in ce.render_md([], 7, 12)


def test_spec_registered_as_free_read() -> None:
    spec = ce.chat_export_spec()
    assert spec.id == "chat.export" and spec.idempotent
    assert spec.params_schema["required"] == []
    assert ce._project_of("studio-p46::tenant1") == 46 and ce._project_of("studio-factory") is None


def test_export_phrase_is_a_question_not_a_task() -> None:
    from app.orchestrator.queue import MessageMode, classify_deterministic

    got = classify_deterministic("выгрузи переписку за сегодня")
    assert got is not None and got.mode is MessageMode.append


def test_metadata_is_free_read() -> None:
    from app.orchestrator.actions import metadata_of

    meta = metadata_of("chat.export")
    assert meta.read_only and not meta.paid and not meta.destructive and meta.risk == "none"


def test_verbatim_reply_keeps_download_links() -> None:
    from app.orchestrator.agents.question_context import keep_verbatim, take_verbatim, verbatim_reply

    result = {
        "text_ru": "Переписка чата за 7 дн.",
        "downloads": [
            {"url": "/api/projects/reports/projects_report_chat_1.md", "file_format": "md"},
            {"url": "/api/projects/reports/projects_report_chat_1.json", "file_format": "json"},
        ],
    }
    text = verbatim_reply("chat.export", result)
    assert text.startswith("Переписка чата за 7 дн.") and "(md)](/api/projects/reports/projects_report_chat_1.md)" in text
    assert "projects_report_chat_1.json" in text
    keep_verbatim("s9", text)
    assert take_verbatim("s9") == text and take_verbatim("s9") is None
