from app.orchestrator.agents.question_context import _OPTIONS, _has, _norm


def test_more_models_phrases_route_to_list():
    for q in ("чем ещё можно генерировать картинки в проекте 12", "из чего выбрать для картинок", "какие модели можно поставить"):
        assert _has(_norm(q), _OPTIONS), q


def test_plain_status_is_not_options():
    assert not _has(_norm("что сейчас с проектом 12"), _OPTIONS)


def test_more_models_phrase_puts_list_models_in_catalog():
    from types import SimpleNamespace

    from app.orchestrator.agents.action_catalog import MODELS_ID, select_ids

    specs = [SimpleNamespace(id=MODELS_ID, title_ru="", description_ru="", tags=())]
    assert MODELS_ID in select_ids("чем ещё можно генерировать картинки в проекте 12", specs, 24)


def test_clarify_keeps_question_with_tech_field_in_brackets():
    from app.orchestrator.agents.summarizer import clarify_text

    out = clarify_text("Не указаны три проекта: уточните их номера (project_id), например 3, 4 и 5.")
    assert "уточните их номера" in out and "project_id" not in out


def test_montage_status_questions_read_the_status():
    from app.orchestrator.agents.question_context import plan_reads

    cases = {
        "какой статус применения правок в проекте 4?": "montage.apply_status",
        "идёт ли сейчас монтаж видео в проекте 4?": "montage.montage_status",
        "какие готовые референсы есть в проекте 4?": "montage.list_ref_assets",
        "как там забор правок из галереи outsee в проекте 4?": "montage.recover_status",
    }
    for q, action in cases.items():
        assert [a for a, _ in plan_reads(q, None)] == [action], q
    assert [a for a, _ in plan_reads("что в очереди правок проекта 10", None)] == ["montage.get_board"]
