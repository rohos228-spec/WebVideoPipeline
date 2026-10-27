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
