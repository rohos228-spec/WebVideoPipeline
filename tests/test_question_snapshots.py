from app.orchestrator.agents.question_context import plan_reads


def test_snapshot_questions_read_journal():
    for text in ("что тут можно откатить, какие снимки есть", "какие есть снимки для отката?",
                 "покажи историю правок, к которым можно вернуться"):
        assert plan_reads(text, 12) == [("history.list_snapshots", {"limit": 20, "project_id": 12})]


def test_story_is_not_snapshots():
    assert all(action != "history.list_snapshots" for action, _ in plan_reads("о чем история в проекте 12?", 12))


def test_snapshots_text_empty():
    import asyncio

    from app.services.action_layer.builtin_wave5_history import _snapshots_text

    assert asyncio.run(_snapshots_text([])) == "Снимков для отката нет."
