from __future__ import annotations

from app.orchestrator.agents.question_context import QuestionContext, plan_reads


class _Nodes:
    async def project_items(self):
        return [{"id": 4, "title": "2 коротких факта о совах", "status": "paused"},
                {"id": 2, "title": "3 факта о котах", "status": "new"}]

    async def rows(self, _pid):
        return []


async def test_factory_question_reads_the_project_named_by_title() -> None:
    seen = []

    async def runner(action, params, _app):
        seen.append((action, params.get("project_id")))
        return {"text_ru": "ок"}

    ctx = QuestionContext(runner=runner, nodes=_Nodes())
    messages = await ctx.messages("в проекте 2 коротких факта о совах какие платные запросы заблокированы?", None)
    assert ("media.list_unknown_outcomes", 4) in seen
    assert all(pid in (4, None) for _a, pid in seen)
    assert "проекте #4" in messages[0]["content"]


def test_cost_question_reads_the_step_cost_basis() -> None:
    reads = plan_reads("сколько будет стоить шаг видео?", 4)
    assert ("step.get_run_cost_basis", {"project_id": 4, "step_code": "video"}) in reads
    reads = plan_reads("сколько стоят промты картинок", 4)
    assert ("step.get_run_cost_basis", {"project_id": 4, "step_code": "img_pr"}) in reads


def test_blocked_question_reads_blocks() -> None:
    assert ("media.list_unknown_outcomes", {"project_id": 4}) in plan_reads("что заблокировано?", 4)


async def test_project_scope_question_about_another_numbered_project_reads_that_project() -> None:
    # Живой прогон: открыт #14, «что с проектом 6?» -> «о проекте #6 данных нет».
    seen = []

    async def runner(action, params, _app):
        seen.append((action, params.get("project_id")))
        return {"text_ru": "ок"}

    class _More(_Nodes):
        async def project_items(self):
            return [*await super().project_items(), {"id": 6, "title": "2 факта о дельфинах", "status": "new"}]

    ctx = QuestionContext(runner=runner, nodes=_More())
    messages = await ctx.messages("что с проектом 6?", 14)
    assert ("project.status", 6) in seen and ("project.status", 14) not in seen
    assert "проекте #6" in messages[0]["content"]
    seen.clear()
    await ctx.messages("что с проектом?", 14)
    assert ("project.status", 14) in seen
    seen.clear()
    await ctx.messages("что с проектом 99?", 14)
    assert ("project.status", 14) in seen
