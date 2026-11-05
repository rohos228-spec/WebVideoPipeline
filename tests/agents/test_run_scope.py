from app.orchestrator.agents.run_scope import run_plan


def _act(goal, pid=4):
    plan = run_plan(goal, pid)
    return plan[0].action if plan else None


def test_remount_phrases():
    for g in ("перемонтируй", "пересобери", "собери заново", "смонтируй ещё раз", "давай пересобери ролик"):
        assert _act(g) == "montage.run_montage", g


def test_full_run_phrases():
    for g in ("доведи до конца", "прогони полностью", "доделай до конца", "прогони 8 проект полностью"):
        assert _act(g) == "queue.enqueue_many", g
    assert run_plan("прогони 8 проект полностью", None)[0].params["project_ids"] == [8]


def test_not_hijacked():
    for g in ("сделай музыку громче и пересобери", "почему не собирается", "собери отчёт по тратам",
              "сделай картинки до конца недели"):
        assert _act(g) is None, g
    assert run_plan("перемонтируй", None) is None


def test_command_after_clarify_is_not_glued():
    from app.orchestrator.queue import answered_request

    prev = [("assistant", "Уточните, какой шаг пересобрать?"), ("user", "перемонтируй")]
    assert answered_request(prev, "перемонтируй") is None
    assert answered_request([("assistant", "Уточните, какой проект?"), ("user", "запусти картинки")], "доведи до конца") is None
    assert answered_request([("assistant", "Уточните, какой проект?"), ("user", "запусти картинки")], "проект 4")


def test_board_not_report_for_frame_questions():
    from app.orchestrator.agents.frame_scope import board_not_report
    from app.orchestrator.contracts import PlanStep

    plan = [PlanStep(action="report.gpt_data", params={"project_id": 10}, mutating=False, risk="none", expected="")]
    assert board_not_report(plan, "сколько длится каждый кадр")[0].action == "montage.get_board"
    assert board_not_report(plan, "выгрузи кадры в эксель")[0].action == "report.gpt_data"
    assert board_not_report(plan, "какие сцены в кадрах")[0].action == "report.gpt_data"


def test_frame_prompt_plans():
    from app.orchestrator.agents.frame_scope import frame_prompt_plan

    show = frame_prompt_plan("покажи промт второго кадра", 10)
    assert show[0].action == "montage.get_board" and show[0].params["prompt_frames"] == [2]
    edit = frame_prompt_plan("поменяй промт кадра 3 на сову в снегу", 10)
    assert edit[0].params["ops"][0] == {"type": "image_regen_prompt", "frame_number": 3, "prompt": "сову в снегу"}
    assert frame_prompt_plan("поменяй промт кадра 3", 10) is None


def test_create_and_run_and_week_report():
    from app.orchestrator.agents.run_scope import clip_report_ids, create_run_plan
    from app.orchestrator.contracts import PlanStep

    plan = create_run_plan("создай ролик про акул и доведи до картинок")
    assert plan[0].action == "project.create" and plan[0].params["title"] == "Ролик про акул"
    assert "доведи его до картинок" in plan[0].expected
    assert [s.action for s in create_run_plan("сделай отчёт за неделю")] == ["projects.overview", "spend.summary"]
    assert create_run_plan("создай ролик про жирафов") is None
    big = [PlanStep(action="report.projects", params={"project_ids": list(range(1, 34))}, mutating=False, risk="none", expected="")]
    assert "project_ids" not in clip_report_ids(big)[0].params


def test_link_and_advice_are_questions():
    from app.orchestrator.queue import _ADVICE_RE, _LINK_RE

    assert _LINK_RE.search("скинь ссылку на ролик")
    assert _ADVICE_RE.match("придумай 5 тем для роликов про природу")


def test_typos_and_small_talk():
    from app.orchestrator.queue import fix_typos
    from app.orchestrator.small_talk import DECLINE_TEXT, GIBBERISH_TEXT, WHO_TEXT, small_talk_reply

    assert fix_typos("зопусти кортинки") == "запусти картинки"
    assert fix_typos("чё с 9") == "что с 9"
    assert fix_typos("все проекты до монтажа доведи") == "запусти все проекты до монтажа"
    assert fix_typos("Запусти картинки в проекте 10") == "Запусти картинки в проекте 10"
    assert small_talk_reply("забей") == DECLINE_TEXT
    assert small_talk_reply("ты кто") == WHO_TEXT
    assert small_talk_reply("асдфгх") == GIBBERISH_TEXT
    assert small_talk_reply("здравствуйте") != GIBBERISH_TEXT
    assert "по Москве" in small_talk_reply("который час")
    assert small_talk_reply("не надо запускать картинки") is None


def test_seed_multi_launch_queue_without_planner():
    from app.orchestrator.agents.launch_scope import seed_multi_launch

    plan = seed_multi_launch("запусти проекты 2, 3, 5, 6, 7, 8, 9 до видео", lambda: list(range(1, 34)))
    assert plan and plan[0].action == "queue.enqueue_many"
    assert plan[0].params["project_ids"] == [2, 3, 5, 6, 7, 8, 9]
    assert plan[0].params["target_node_type"] == "videos"
    # Несуществующий номер — решает планировщик (он скажет, что такого нет).
    assert seed_multi_launch("запусти проекты 2, 300 до видео", lambda: list(range(1, 34))) is None
    assert seed_multi_launch("запусти проект 5 до видео", lambda: list(range(1, 34))) is None


def test_other_number_with_preposition_s():
    from app.orchestrator.agents.question_context import _OTHER_NUMBER_RE

    assert _OTHER_NUMBER_RE.search("что с 13")
    assert _OTHER_NUMBER_RE.search("а про 9?")


def test_drop_missing_projects_from_queue():
    from app.orchestrator.agents.launch_scope import drop_missing_projects
    from app.orchestrator.agents.planner import PlanStep

    par = PlanStep(action="queue.set_parallel", params={"workers": 2}, mutating=True, risk="medium", expected="")
    q = PlanStep(action="queue.enqueue_many", params={"project_ids": [2, 300]}, mutating=True, risk="high", expected="")
    notes: list[str] = []
    out = drop_missing_projects([par, q], lambda: [1, 2, 3], notes)
    assert [s.action for s in out] == ["queue.enqueue_many"] and out[0].params["project_ids"] == [2]
    assert notes == ["Проекта #300 в студии нет, его не ставлю."]
    notes = []
    q2 = PlanStep(action="queue.enqueue_many", params={"project_ids": [300, 400]}, mutating=True, risk="high", expected="")
    assert drop_missing_projects([par, q2], lambda: [1, 2], notes) == []
    assert "#300, #400" in notes[0]


def test_video_frame_plan_regen_one_frame():
    from app.orchestrator.agents.frame_scope import video_frame_plan

    plan = video_frame_plan("перезапусти видео кадра 2", 10)
    assert plan and plan[0].params["ops"] == [{"type": "video_regen", "frame_number": 2}]
    assert video_frame_plan("видео кадра 5 кривое, переделай", 10)[0].params["ops"][0]["frame_number"] == 5
    assert video_frame_plan("сделай видео для всех кадров", 10) is None
    assert video_frame_plan("какое видео у кадра 2", 10) is None


def test_launch_with_price_question_and_batch_then_run():
    from app.orchestrator.queue import fix_typos

    assert fix_typos("запусти 5 и скажи сколько это будет стоить") == "запусти проект 5"
    assert fix_typos("запусти 5 кадров") == "запусти 5 кадров"
    out = fix_typos("сделай 10 роликов про собак и запусти все")
    assert out.startswith("сделай 10 роликов про собак") and "запусти все" not in out


def test_follow_up_reads_use_previous_question():
    from app.orchestrator.queue import _follow_up_reads

    dialog = [
        {"role": "user", "content": "что с проектом 5"},
        {"role": "assistant", "content": "x"},
        {"role": "user", "content": "а с 7?"},
        {"role": "assistant", "content": "y"},
    ]
    assert _follow_up_reads("а что дешевле из них доделать", dialog).endswith("(проекты 5, 7)")
    assert _follow_up_reads("а другие какие есть", [{"role": "user", "content": "какой голос"}]).startswith("какой голос")
    assert _follow_up_reads("а с 9?", dialog) == "а с 9?"


def test_fact_check_and_cheaper_regex():
    from app.orchestrator.agents.question_context import _FACT_CHECK_RE
    from app.orchestrator.queue import _CHEAPER_RE

    assert _FACT_CHECK_RE.search("проверь факты в тексте")
    assert not _FACT_CHECK_RE.search("сделай 2 факта о котах")
    assert _CHEAPER_RE.search("сделай подешевле")
    assert _CHEAPER_RE.search("как сэкономить")
    assert not _CHEAPER_RE.search("поменяй модель видео на подешевле")


def test_image_frame_plan_regen_one_frame():
    from app.orchestrator.agents.frame_scope import image_frame_plan

    assert image_frame_plan("кадр 4 переделай", 10)[0].params["ops"] == [{"type": "image_regen", "frame_number": 4}]
    assert image_frame_plan("кит на 2 кадре какой-то кривой, перегенери", 10)[0].params["ops"][0]["frame_number"] == 2
    assert image_frame_plan("в кадре 2 сделай сову белой", 10)[0].params["ops"][0]["type"] == "image_regen_correction"
    assert image_frame_plan("почему кадр 5 плохой?", 10) is None
    assert image_frame_plan("перезапусти видео кадра 2", 10) is None


def test_card_text_names_queue_ops():
    from app.orchestrator.agents.approval_steps import _params_text

    text = _params_text({"project_id": 9, "ops": [{"type": "video_regen", "frame_number": 2}]})
    assert text.startswith("видео кадра 2 заново")


def test_frame_fix_plan_correction():
    from app.orchestrator.agents.frame_scope import image_frame_plan

    op = image_frame_plan("в кадре 2 сделай сову белой в проекте 10", 4)[0]
    assert op.params["project_id"] == 10
    assert op.params["ops"] == [{"type": "image_regen_correction", "frame_number": 2, "prompt": "сделай сову белой"}]
    assert image_frame_plan("переделай кадр 3, сделай темнее", 10)[0].params["ops"][0]["prompt"] == "сделай темнее"
    assert image_frame_plan("сделай кадры по 3 секунды", 10) is None
    assert image_frame_plan("удали кадр 3", 10) is None
