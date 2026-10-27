from app.services.action_layer.planner_catalog import SYNONYMS


def test_thinking_words_reach_effort_actions():
    assert "дума" in SYNONYMS["node.set_params_costly"].split()
    assert "посильн" in SYNONYMS["node.set_params_costly"].split()
    assert "дума" in SYNONYMS["node.set_params"].split()
    assert "куск" in SYNONYMS["group.list"].split()


def test_parallel_casual_words():
    assert {"крут", "максимум"} <= set(SYNONYMS["queue.set_parallel"].split())
