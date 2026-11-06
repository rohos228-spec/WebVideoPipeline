"""Чат проекта: человек не видит кодов шагов, ключей нод и технических блоков кода."""

from app.services.chat_humanize import StreamHumanizer, build_labels, humanize_reply, step_title

KEYMAP = {
    "n_plan": {"type": "plan", "label": "Сценарий"},
    "n_excel_gpt_1": {"type": "excel_gpt", "label": "Работа с GPT"},
    "n_excel_gpt_2": {"type": "excel_gpt", "label": "Работа с GPT"},
    "n_image_prompts": {"type": "image_prompts", "label": "Промты картинок"},
    "n_images": {"type": "images", "label": "Мои картинки"},
}

# Ответы модели, пойманные вживую в чате проекта до правки.
LIVE = (
    "Последний прогон имеет статус `waiting_hitl`, но среди статусов нод нет HITL-ноды. "
    "Последняя проверка harness завершилась успешно. Следующий шаг по пайплайну — `img`, "
    "генерация картинок. После неё идут `anim_pr`, `video` и `publish`."
)


def test_step_codes_become_canvas_titles():
    out = humanize_reply(LIVE, KEYMAP)
    for raw in ("`", "waiting_hitl", "HITL", "harness", "anim_pr"):
        assert raw not in out
    assert "«Мои картинки»" in out  # подпись с канваса важнее названия по умолчанию
    assert "«Промты анимации»" in out and "«Публикация»" in out
    assert "статус «ждёт проверки человеком»" in out
    assert "нет ноды «Проверка человеком»" in out
    assert "Последняя автопроверка завершилась" in out


def test_node_keys_and_twins_get_numbers():
    out = humanize_reply("Ноды n_excel_gpt_1 и `n_excel_gpt_2` готовы, `n_plan` тоже.", KEYMAP)
    assert out == "Ноды «Работа с GPT 1» и «Работа с GPT 2» готовы, «Сценарий» тоже."


def test_duplicate_parenthetical_collapses():
    out = humanize_reply(
        "Текущий шаг — `img_pr` («Промты картинок»), дальше `img` (генерация картинок).", KEYMAP
    )
    assert out == "Текущий шаг — «Промты картинок», дальше «Мои картинки»."


def test_code_blocks_removed_unless_fixing_code():
    text = "Причина найдена.\n```python\ndef f():\n    return 1\n```\nСмотри `app/web/api.py`."
    out = humanize_reply(text, KEYMAP)
    assert "def f" not in out and "`" not in out
    assert "app/web/api.py" in out  # путь остаётся словом, но без оформления кодом
    kept = humanize_reply(text, KEYMAP, keep_code=True)
    assert "```python" in kept and "`app/web/api.py`" in kept


def test_plain_words_untouched():
    text = "Видео будет готово, music и video в тексте — обычные слова, plan тоже."
    assert humanize_reply(text, KEYMAP) == text


def test_stream_matches_final_and_keeps_json_tail():
    raw = LIVE + '\n\n{"actions":[{"run_step":"img"}]}'
    for size in (1, 3, 7, 40):
        s = StreamHumanizer(KEYMAP)
        shown = "".join(s.feed(raw[i : i + size]) for i in range(0, len(raw), size)) + s.flush()
        head, tail = shown.split('{"actions"', 1)
        assert head.strip() == humanize_reply(LIVE, KEYMAP)
        assert tail == ':[{"run_step":"img"}]}'


def test_step_title_and_defaults():
    assert step_title("plan") == "Сценарий"
    assert step_title("script") == "Закадровый текст"
    assert step_title("img", KEYMAP) == "Мои картинки"
    assert step_title("enrich_3") == "Обогащение 3"
    assert build_labels(None)["auto_mode"] == "автоматический режим"


def test_node_key_not_on_canvas_falls_back_to_type_title():
    # Ключ из текста ошибки старого проекта, на канвасе такой ноды нет.
    out = humanize_reply("нода n_excel_gpt_1 упала, n_images ждёт, n_foo_3 — неизвестно что", {})
    assert out == "нода «Работа с GPT 1» упала, «Картинки» ждёт, n_foo_3 — неизвестно что"


def test_set_prompt_note_names_the_step_not_its_code():
    from app.services.project_chat_guard import action_note

    note = action_note({"set_prompt": "script→nii67"}, {})
    assert "script" not in note and "«Закадровый текст»" in note and "«nii67»" in note


def test_chat_can_add_style_agent_nodes_with_russian_labels():
    from app.web.routers.db_browser import _all_node_types, _node_type_ru

    assert {"style_extract", "hitl_style"} <= _all_node_types()
    assert _node_type_ru("style_extract") == "Разбор стиля"
    assert _node_type_ru("hitl_style") == "Проверка стиля"


def test_action_title_and_note_for_excel_gpt_node():
    from app.services.project_chat_guard import action_note, action_title

    km = {"n_excel_gpt_1": {"type": "excel_gpt", "label": "ИИ-редактор сцен"}}
    act = {"run_step": "excel_gpt", "node_key": "n_excel_gpt_1"}
    assert action_title(act, km) == "Запустить шаг «ИИ-редактор сцен»."
    note = action_note({"run_step": "excel_gpt", "node_key": "n_excel_gpt_1", "step_label": "ИИ-редактор сцен"}, km)
    assert note == "запущен шаг «ИИ-редактор сцен»"

    act_node = {"run_node": "n_excel_gpt_1"}
    assert action_title(act_node, km) == "Запустить шаг «ИИ-редактор сцен»."
    note_node = action_note({"run_node": "n_excel_gpt_1", "node_key": "n_excel_gpt_1"}, km)
    assert note_node == "запущен шаг «ИИ-редактор сцен»"


def test_pipeline_state_lines_uses_actual_canvas_graph():
    from app.models import Project, ProjectStatus
    from app.orchestrator.default_graph import default_graph
    from app.web.routers.db_browser import _pipeline_state_lines

    nodes, edges = default_graph()
    p = Project(id=1, slug="demo", status=ProjectStatus.frames_ready)
    status_by_key = {"n_plan": "done", "n_script": "done", "n_split": "done", "n_excel_gpt_1": "pending"}

    lines = _pipeline_state_lines(p, nodes=nodes, edges=edges, status_by_key=status_by_key)
    full = "\n".join(lines)

    # Не должно быть фантомных шагов веера, которых нет на дефолтном канвасе
    assert "Агент сцен" not in full
    assert "scene_d" not in full
    assert "Сборка сцен" not in full
    assert "scene_asm" not in full

    # Должен быть реальный ИИ-редактор сцен с его node_key
    assert "- «Разбивка» (код для JSON: split): готово" in lines
    assert "- «ИИ-редактор сцен» (код для JSON: excel_gpt, node_key: n_excel_gpt_1): ждёт" in lines
    assert "- «Персонажи» (код для JSON: hero): ждёт" in lines
