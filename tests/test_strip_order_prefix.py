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


def test_board_text_shows_which_frames_have_images():
    from app.orchestrator.agents.summarizer import _board_text

    text = _board_text(
        {
            "frame_count": 2,
            "frames": [
                {"number": 1, "voiceover_text": "а", "has_image": True, "has_video": False},
                {"number": 2, "voiceover_text": "б", "has_image": False, "has_video": False},
            ],
            "frames_with_image": 1,
            "frames_with_video": 0,
        }
    )
    assert "кадр 1 [есть картинка]" in text and "кадр 2 [без картинки]" in text
    assert "Картинки есть у 1 из 2" in text


def test_missing_action_drops_add_this_action_advice():
    from app.orchestrator.agents.summarizer import NOT_SUPPORTED, missing_action_text

    reason = "В каталоге нет действия для шага; нужно добавить это действие, чтобы запустить шаг с лимитом."
    assert missing_action_text(reason) == NOT_SUPPORTED


def test_queue_hint_dropped_when_queue_applied_in_same_task():
    from app.orchestrator.agents.summarizer import StepNote, _queue_ops_text, _superseded

    queued = StepNote("montage.queue_ops", "q", ok=True, text=_queue_ops_text({"queued": 1, "total_in_queue": 1}))
    applied = StepNote("montage.apply_queue", "a", ok=True, text="Готово")
    assert "примени очередь" in queued.text
    out = _superseded([queued, applied])
    assert "примени очередь" not in out[0].text and out[0].text.startswith("Поставил в очередь")
    assert "примени очередь" in _superseded([queued])[0].text
