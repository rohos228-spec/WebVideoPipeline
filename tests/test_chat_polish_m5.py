from app.orchestrator.agents.action_catalog import _frame_hints
from app.orchestrator.queue import is_go_ahead
from app.services.orch_bridge import _frame_note, goal_limit, over_goal_limit


def test_done_text_names_frame():
    assert _frame_note({"frame_numbers": [1]}) == " (кадр 1)"
    assert _frame_note({"frame_numbers": [1, 2]}) == " (кадры 1, 2)"
    assert _frame_note({"frame_number": 5}) == " (кадр 5)"
    assert _frame_note({"project_id": 3}) == ""


def test_goal_limit_parsed_only_in_dollars():
    assert goal_limit("сгенерируй видео кадра 1, лимит $0.2") == 0.2
    assert goal_limit("лимит 0,5$") == 0.5
    assert goal_limit("лимит 100 рублей") is None
    assert goal_limit("сделай картинки") is None


def test_card_warns_when_estimate_above_goal_limit():
    assert "$0.94" in over_goal_limit(0.94, "видео кадра 1, лимит $0.2")
    assert over_goal_limit(0.1, "видео кадра 1, лимит $0.2") == ""
    assert over_goal_limit(None, "лимит $0.2") == ""


def test_go_on_words_are_go_ahead():
    assert is_go_ahead("давай дальше")
    assert is_go_ahead("продолжай")
    assert not is_go_ahead("давай дальше картинки для кадра 3 и видео")


def test_shot_hint_for_queue_ops_without_frame_id():
    rows = [{"p": "project_id*:int,ops*:[{type*:{video_regen},frame_number*:int,shot:{1|2}}]"}]
    assert any("шот" in line.lower() for line in _frame_hints(rows))
    assert _frame_hints([{"p": "project_id*:int,screenshot:str"}]) == []


def test_coverage_role_in_russian_becomes_child_or_parent():
    from app.services.action_layer.planner_rules import _normalize_values

    out = {"type": "coverage_kind", "value": "дочерний", "parent_number": 1}
    _normalize_values("montage.set_coverage", out, [])
    assert out == {"type": "coverage_kind", "value": "child", "parent_number": 1}
    out = {"type": "coverage_kind", "value": "Родитель"}
    _normalize_values("montage.set_coverage", out, [])
    assert out["value"] == "parent"


def test_coverage_role_text_is_russian():
    from app.orchestrator.agents.summarizer import _coverage_text

    body = {"ok": True, "type": "coverage_kind", "value": "child", "frame_number": 2}
    assert _coverage_text(body) == "Кадр 2: тип кадра — дочерний."


def test_delete_request_gets_a_clear_refusal():
    from app.orchestrator.agents.summarizer import DELETE_REFUSED, delete_refusal_text

    for q in ("удали кадр 5 в проекте 12", "в проекте 12 убери кадр 6 совсем", "сотри кадр 7 из проекта 12"):
        assert delete_refusal_text(q) == DELETE_REFUSED, q
    for q in ("убери реф с кадра 1", "сделай кадр 2 дочерним", "убери все правки из очереди"):
        assert delete_refusal_text(q) == "", q


def test_light_words_normalized_to_catalog():
    from app.services.action_layer.planner_rules import _normalize_values

    for raw, want in (("ночное", "ночной"), ("закатный", "закат"), ("рассветный", "рассвет")):
        out = {"type": "coverage_light", "value": raw}
        _normalize_values("montage.set_coverage", out, [])
        assert out["value"] == want


def _queue_step(text):
    from app.orchestrator.contracts import PlanStep

    ops = [{"type": "image_regen_correction", "frame_number": 8, "correction": text}]
    return [PlanStep(action="montage.queue_ops", params={"project_id": 12, "ops": ops}, mutating=True)]


def test_light_phrase_is_coverage_not_redraw():
    from app.orchestrator.agents.light_scope import light_as_coverage

    plan = light_as_coverage(_queue_step("ночное освещение"), "проект 12, кадр 8: ночное освещение")
    assert [(s.action, s.params["type"], s.params["value"], s.params["frame_number"]) for s in plan] == [
        ("montage.set_coverage", "coverage_light", "ночной", 8)
    ]


def test_redraw_request_stays_in_queue():
    from app.orchestrator.agents.light_scope import light_as_coverage

    plan = light_as_coverage(_queue_step("ночное освещение"), "перерисуй картинку кадра 8 с ночным освещением")
    assert plan[0].action == "montage.queue_ops"


def test_frame_value_in_quotes_is_a_task():
    from app.orchestrator.queue import classify_deterministic

    for q in ("в проекте 12 у кадра 14 действие: «сова поворачивает голову»", "кадр 15: опиши действие «птенец открывает клюв»"):
        assert classify_deterministic(q).mode.value == "task", q
    assert classify_deterministic("какой закадр у кадра 3 «тест»?").mode.value != "task"
