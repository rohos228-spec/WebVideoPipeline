from app.orchestrator import queue as q


def test_go_ahead_words():
    assert q.is_go_ahead("да, запускай")
    assert q.is_go_ahead("ок")
    assert q.is_go_ahead("давай")
    assert not q.is_go_ahead("да запусти картинки")
    assert not q.is_go_ahead("")


def test_suggested_command_from_answer_tail():
    text = "Следующий шаг — закадровый текст.\n«запусти закадровый текст в проекте 26»"
    assert q.suggested_command(text) == "запусти закадровый текст в проекте 26"
    assert q.suggested_command("Следующий шаг — «Сценарий».") is None
    assert q.suggested_command("Шаг «Сценарий» завершён, статус проекта готов.") is None
    assert q.suggested_command(None) is None


def test_step_verbs_are_tasks():
    from app.orchestrator.contracts import MessageMode

    for text in ("разбей 26 на кадры", "озвучь проект 26", "смонтируй проект 12"):
        decided = q.classify_deterministic(q.canon_project_refs(text))
        assert decided is not None and decided.mode is MessageMode.task, text


def test_spend_limit_means_task():
    from app.orchestrator.contracts import MessageMode

    decided = q.classify_deterministic(q.canon_project_refs("теперь промты картинок для 26, лимит $0.5"))
    assert decided is not None and decided.mode is MessageMode.task
    asked = q.classify_deterministic(q.canon_project_refs("какой лимит $5 стоит?"))
    assert asked is None or asked.mode is not MessageMode.task
