from app.services.action_layer.planner_catalog import SYNONYMS


def test_thinking_words_reach_effort_actions():
    assert "дума" in SYNONYMS["node.set_params_costly"].split()
    assert "посильн" in SYNONYMS["node.set_params_costly"].split()
    assert "дума" in SYNONYMS["node.set_params"].split()
    assert "куск" in SYNONYMS["group.list"].split()


def test_parallel_casual_words():
    assert {"крут", "максимум"} <= set(SYNONYMS["queue.set_parallel"].split())


def test_step_words_offer_run_step():
    from app.services.action_layer.planner_catalog import select_catalog
    from app.services.action_layer.spec import ensure_builtin_actions, registry

    ensure_builtin_actions()
    for text in ("подготовь промты анимации для 26, лимит $0.5", "разбей 26 на кадры"):
        assert "step.run_step" in select_catalog(text, None, registry, 30).ids, text
