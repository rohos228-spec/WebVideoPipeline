from app.orchestrator.queue import canon_project_refs


def test_number_before_word_and_typo():
    assert canon_project_refs("че с 12м проектом") == "че с проектом 12"
    assert canon_project_refs("что в 12-м проекте") == "что в проекте 12"
    assert canon_project_refs("пачему праект 12 стаит") == "пачему проект 12 стаит"


def test_counts_untouched():
    for text in ("покажи 3 проекта", "у меня 5 проектов", "в 2 проектах", "#12 проект"):
        assert canon_project_refs(text) == text


def test_planner_gets_named_project():
    from app.orchestrator.agents.action_catalog import named_other_project

    assert named_other_project("почему проект 12 стоит", 25) == 12
    assert named_other_project("#12 сколько потрачено", 25) == 12
    assert named_other_project("что с проектом 25", 25) is None
    assert named_other_project("проекты 12 и 14", 25) is None
    assert named_other_project("что тут", 25) is None
