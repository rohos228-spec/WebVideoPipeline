from app.orchestrator.agents.summarizer import drop_echo, strip_order_prefix


def test_order_prefix_cut():
    assert strip_order_prefix("Проверь проект #12: он стоит на паузе.") == "Проект #12 стоит на паузе."
    assert strip_order_prefix("Проверьте журнал сервера проекта #12: да, есть падения.") == "Да, есть падения."
    assert strip_order_prefix("Покажи снимки для отката в проекте 12: доступно 20 снимков.") == "Доступно 20 снимков."


def test_plain_lines_kept():
    for text in ("Ответ: 5 кадров.", "Напишите «запусти картинки»: чат покажет карточку.", "Покажу итог: всё ок"):
        assert strip_order_prefix(text) == text


def test_drop_echo_keeps_answer_after_prefix():
    out = drop_echo("Проверь проект #12: он стоит на паузе из-за «промтов картинок».", "что с 12")
    assert out == "Проект #12 стоит на паузе из-за «промтов картинок»."


def test_missing_action_keeps_other_reason():
    from app.orchestrator.agents.summarizer import NOT_SUPPORTED, missing_action_text

    reason = (
        "Нельзя безопасно запланировать прогон до промтов картинок: в проекте 26 сейчас 0 кадров, "
        "сначала нужна разбивка на кадры; кроме того, действие очереди отсутствует в доступном каталоге."
    )
    out = missing_action_text(reason)
    assert out.startswith(NOT_SUPPORTED) and "0 кадров" in out and "каталог" not in out
    assert missing_action_text("Такого действия нет в каталоге.") == NOT_SUPPORTED


def test_missing_action_drops_developer_advice():
    from app.orchestrator.agents.summarizer import NOT_SUPPORTED, missing_action_text

    reason = "В каталоге нет действия для шага «Разбивка»; нужно добавить его или уточнить доступный способ запуска."
    assert missing_action_text(reason) == NOT_SUPPORTED
