"""Ответы чата опираются на факты в обоих режимах: «Проект» и «Вся фабрика»."""
from __future__ import annotations

from app.orchestrator.agents.project_nodes import numbered_projects
from app.orchestrator.agents.question_context import ANSWER_RULES, EXAMPLE_COMMAND, QuestionContext, plan_reads
from app.orchestrator.contracts import MessageMode
from app.orchestrator.queue import classify_deterministic


class _Nodes:
    async def project_items(self):
        return [{"id": 10, "title": "2 факта о совах-неясытях", "status": "paused"},
                {"id": 6, "title": "2 факта о дельфинах", "status": "enrich_2_ready"}]

    async def rows(self, _pid):
        return []


def test_next_step_question_in_project_scope_reads_project_status() -> None:
    for question in ("что дальше?", "какой следующий шаг", "что осталось?"):
        assert "project.status" in [a for a, _ in plan_reads(question, 6)], question


def test_capabilities_and_attention_questions_read_the_projects() -> None:
    for question in ("что ты умеешь?", "какие требуют внимания"):
        assert "projects.overview" in [a for a, _ in plan_reads(question, None)], question


async def test_factory_spend_question_uses_project_number_from_text() -> None:
    seen = []

    async def runner(action, params, _app):
        seen.append((action, dict(params)))
        return {"text_ru": "ок"}

    ctx = QuestionContext(runner=runner, nodes=_Nodes())
    messages = await ctx.messages("сколько потрачено на проект 10", None)
    assert ("spend.summary", {"project_id": 10}) in seen
    assert "проекте #10" in messages[0]["content"]


async def test_factory_spend_question_without_number_reads_whole_factory() -> None:
    seen = []

    async def runner(action, params, _app):
        seen.append((action, dict(params)))
        return {"text_ru": "ок"}

    ctx = QuestionContext(runner=runner, nodes=_Nodes())
    await ctx.messages("сколько потрачено сегодня?", None)
    assert ("spend.summary", {}) in seen


async def test_factory_spend_question_uses_hash_number() -> None:
    """«#10» без слова «проект» - тоже номер проекта; чужой номер - траты всей фабрики."""
    seen = []

    async def runner(action, params, _app):
        seen.append((action, dict(params)))
        return {"text_ru": "ок"}

    ctx = QuestionContext(runner=runner, nodes=_Nodes())
    await ctx.messages("сколько потрачено на #10", None)
    assert ("spend.summary", {"project_id": 10}) in seen
    seen.clear()
    await ctx.messages("сколько потрачено на #99", None)
    assert ("spend.summary", {}) in seen


def test_answer_rules_suggest_a_phrase_that_the_chat_runs() -> None:
    rules = " ".join(ANSWER_RULES)
    assert "предложи написать это задачей" not in rules
    assert EXAMPLE_COMMAND in rules
    decided = classify_deterministic(EXAMPLE_COMMAND)
    assert decided is not None and decided.mode is MessageMode.task
    assert numbered_projects([{"id": 6, "title": "2 факта о дельфинах"}], EXAMPLE_COMMAND) == [6]


def test_answer_rules_tell_how_to_open_a_project() -> None:
    assert "слева в списке" in " ".join(ANSWER_RULES)



def test_open_project_request_is_answered_not_planned() -> None:
    """«открой муравьев» идёт ответом с подсказкой, а не задачей без правила в планировщике."""
    for text in ("открой муравьев", "откройте проект 6"):
        decided = classify_deterministic(text)
        assert decided is not None and decided.mode is MessageMode.append, text
    decided = classify_deterministic("открой проект 6 и запусти картинки")
    assert decided is not None and decided.mode is MessageMode.task
