"""Тупики чата оркестратора на живых вопросах владельца.

- «картинки без стиля пластилин, найди где он теряется» — проверка стиля в промтах;
- «промты картинок отработали за 3 секунды» — разбор ноды по шагу и время прогона;
- «я ноду запускал!» — рассказ о прошлом запуске, не просьба запустить.
"""

from __future__ import annotations

from app.orchestrator.agents.question_context import plan_reads
from app.orchestrator.queue import _PAST_RUN_RE
from app.services import content_checks as cc
from app.services.action_layer.node_failure import format_seconds, node_for_step, run_seconds


def _rows(*prompts: str) -> list[dict]:
    return [{"number": i + 1, "uuid": f"u{i}", "prompt": p} for i, p in enumerate(prompts)]


def test_style_missing_against_project_style() -> None:
    rows = _rows("minimalist claymation plasticine owl", "a dark forest at night", "plasticine fox")
    found = cc.style_findings(rows, "пластилин")
    assert len(found) == 1
    assert found[0]["check"] == "prompt_style"
    assert found[0]["frames"] == [2]


def test_style_majority_without_settings() -> None:
    rows = _rows("plasticine owl", "plasticine fox", "photorealistic noir archival street")
    found = cc.style_findings(rows, None)
    assert found and found[0]["frames"] == [3]
    assert "нуар" in found[0]["detail_ru"]


def test_style_silent_when_no_style_anywhere() -> None:
    assert cc.style_findings(_rows("an owl", "a fox"), None) == []
    assert cc.style_findings(_rows("plasticine owl", "plasticine fox"), "пластилин") == []


def test_style_finding_has_no_paid_rerun() -> None:
    report = cc.make_report("img_pr", cc.style_findings(_rows("owl"), "пластилин"), frames=1)
    actions = [o.get("action") for o in cc.fix_options(report, 7)]
    assert "step.run_step" not in actions
    assert "без стиля" in cc.summary_text(report) or "не написан" in cc.summary_text(report)


def test_style_question_reads_prompt_checks() -> None:
    reads = plan_reads("у меня картинки без стиля пластилин, найди где он теряется", 46)
    assert ("diag.content_checks", {"project_id": 46, "step": "img_pr"}) in reads


def test_fast_run_question_reads_failure_by_step() -> None:
    reads = plan_reads("промты картинок отработали за 3 секунды, что случилось", 46)
    failure = [p for a, p in reads if a == "diag.node_failure"]
    assert failure and failure[0].get("step") == "img_pr"


def test_past_run_is_not_a_command() -> None:
    assert _PAST_RUN_RE.search("промты картинок я говорю ноду запускал!")
    assert _PAST_RUN_RE.search("ну я запустил, он отработал 3 секунды")
    assert not _PAST_RUN_RE.search("запусти промты картинок")
    assert not _PAST_RUN_RE.search("запускай дальше")


def test_run_seconds_and_format() -> None:
    nr = {"started_at": "2026-11-05T10:00:00", "finished_at": "2026-11-05T10:00:03"}
    assert run_seconds(nr) == 3
    assert run_seconds({"started_at": None}) is None
    assert format_seconds(3) == "3 с"
    assert format_seconds(125) == "2 мин 5 с"


def test_node_for_step_prefers_last_run() -> None:
    meta = {
        "canvas_graph": {
            "nodes": [
                {"id": "n_a", "type": "img_pr"},
                {"id": "n_b", "type": "img_pr"},
                {"id": "n_c", "type": "img"},
            ],
            "edges": [],
        }
    }
    runs = [
        {"node_key": "n_a", "finished_at": "2026-11-05T10:00:00"},
        {"node_key": "n_b", "finished_at": "2026-11-05T11:00:00"},
    ]
    assert node_for_step(meta, runs, "img_pr") == "n_b"
    assert node_for_step(meta, [], "img_pr") == "n_a"
    assert node_for_step(meta, runs, "video") is None


def test_summary_says_style_present_or_absent() -> None:
    ok = cc.make_report("img_pr", [], frames=2)
    ok["style"] = cc.style_state(_rows("plasticine owl", "plasticine fox"), None)
    assert "во всех 2 промтах" in cc.summary_text(ok)
    none = cc.make_report("img_pr", [], frames=2)
    none["style"] = cc.style_state(_rows("an owl", "she felt a fox"), None)
    assert "Ни в одном из 2" in cc.summary_text(none)


def test_style_question_task_gets_style_check() -> None:
    from app.orchestrator.agents.failure_scope import content_check_scope
    from app.orchestrator.contracts import PlanStep

    plan = [PlanStep(action="report.gpt_data", params={"project_id": 10}, expected="таблица")]
    for goal in ("стиль куда то пропал в промтах, глянь", "у меня картинки без стиля, найди где он теряется"):
        out = content_check_scope(plan, goal)
        assert out[0].action == "diag.content_checks" and out[0].params == {
            "project_id": 10,
            "step": "img_pr",
        }


def test_check_prompts_without_number_uses_plan_project() -> None:
    from app.orchestrator.agents.failure_scope import content_check_scope
    from app.orchestrator.contracts import PlanStep

    plan = [PlanStep(action="report.gpt_data", params={"project_id": 10}, expected="таблица")]
    out = content_check_scope(plan, "проверь промпты картинак")
    assert [s.action for s in out] == ["diag.content_checks"]


def test_frame_numbers_from_phrase() -> None:
    from app.orchestrator.agents.data_report import frame_numbers

    assert frame_numbers("покажи промт третьего кадра") == [3]
    assert frame_numbers("какой промт у 7 кадра") == [7]
    assert frame_numbers("кадр №12 и кадр 4") == [4, 12]
    assert frame_numbers("сделай картинки для первых трех кадров") == []


def test_capability_questions_get_help() -> None:
    from app.orchestrator.small_talk import HELP_TEXT, small_talk_reply

    for text in ("а ты вообще что умеешь", "че ты можешь?", "что ты умеешь делать вообще"):
        assert small_talk_reply(text) == HELP_TEXT
    assert small_talk_reply("что ты можешь сделать с проектом 4") is None


def test_role_and_past_questions_are_not_tasks() -> None:
    from app.orchestrator.queue import classify_deterministic

    for text in (
        "что делает нода работа с гпт",
        "зачем нужна нода персонажи",
        "я запускал промты картинок",
        "а что делает разбивка",
    ):
        got = classify_deterministic(text)
        assert got is not None and got.mode.value == "append", text


def test_prompt_length_and_split_questions_route_to_checks() -> None:
    assert ("diag.content_checks", {"project_id": 10, "step": "img_pr"}) in plan_reads(
        "почему промты такие короткие", 10
    )
    assert ("diag.content_checks", {"project_id": 10, "step": "split"}) in plan_reads(
        "почему разбивка дала 28 кадров", 10
    )
    assert ("diag.content_checks", {"project_id": 10, "step": "split"}) in plan_reads(
        "откуда столько кадров", 10
    )


def test_summary_explains_prompt_sizes_and_frame_counts() -> None:
    img = cc.make_report("img_pr", [], frames=2)
    img["prompt_sizes"] = {"min": 100, "median": 150, "max": 200, "n": 2}
    assert "от 100 до 200 знаков" in cc.summary_text(img)
    split = cc.make_report("split", [], frames=28)
    split["counts"] = {"frames": 28, "with_vo": 20, "requested": None}
    text = cc.summary_text(split)
    assert "с закадровым текстом 20" in text and "ещё 8" in text


def test_gateway_error_in_body_is_provider_error() -> None:
    import pytest

    from app.orchestrator.routing.transport import ProviderError, gateway_error_text, parse_chat_payload

    body = "这把密钥不能用模型「gpt-5.6-sol」。 (request id: 202611051016195386274358268d9d6u8zwHY1O)"
    assert gateway_error_text(body)
    assert not gateway_error_text("Проект #10 стоит: автозапуск выключен.")
    with pytest.raises(ProviderError):
        parse_chat_payload({"choices": [{"message": {"content": body}}]})


def test_human_error_hides_exception_names() -> None:
    from app.orchestrator.agents.summarizer import human_error

    raw = "ActionCallError: HTTP 404: {'code': 'node_not_found', 'message': 'Ноды «n_split» нет на холсте проекта.'}"
    assert human_error(raw) == "Ноды «n_split» нет на холсте проекта"


def test_cyrillic_gpt_finds_gpt_node():
    from app.orchestrator.agents.project_nodes import match_nodes

    rows = [
        {"key": "n_excel_gpt_1", "type": "excel_gpt", "type_ru": "Работа с GPT", "label": "Работа с GPT"},
        {"key": "n_split", "type": "split", "type_ru": "Разбивка", "label": "Разбивка"},
    ]
    assert match_nodes(rows, "что делает нода работа с гпт") == ["n_excel_gpt_1"]


def test_missing_project_is_named_plainly():
    from app.orchestrator.agents.question_context import _missing_projects

    known = set(range(1, 34))
    assert "Проекта #46 в студии нет" in (_missing_projects("что с 46 проектом", known) or "")
    assert "от #1 до #33" in (_missing_projects("что с проектом 46?", known) or "")
    assert _missing_projects("что с 4 проектом", known) is None
    assert _missing_projects("сравни 5 и 7 проект", known) is None


def test_number_before_word_project_resolves():
    import asyncio

    from app.orchestrator.agents.question_context import QuestionContext

    class Nodes:
        async def project_items(self):
            return [{"id": 4, "title": "совы"}, {"id": 10, "title": "ежи"}]

    ctx = QuestionContext(nodes=Nodes())
    assert asyncio.run(ctx._named_project("хз че делать, видео не собирается в 4 проекте")) == 4


def test_priciest_project_reads_spend():
    from app.orchestrator.agents.question_context import plan_reads

    assert ("spend.summary", {}) in plan_reads("где самый дорогой проект", None)
