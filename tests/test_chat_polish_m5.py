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
