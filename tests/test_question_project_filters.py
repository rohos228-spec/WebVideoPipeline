import asyncio

from app.orchestrator.agents import question_context as qc


def test_overview_status_filter():
    assert qc._overview("какие проекты сейчас на паузе") == {"status": "paused", "limit": 50}
    assert qc._overview("какие проекты упали") == {"status": "attention", "limit": 50}
    assert qc._overview("покажи все проекты") == {"limit": 15}


class _Nodes:
    async def project_items(self):
        return [{"id": 12, "title": "2 факта о муравьях", "status": "paused"}, {"id": 25, "title": "тест", "status": "new"}]


def test_tail_number_is_project():
    ctx = qc.QuestionContext(nodes=_Nodes())
    assert asyncio.run(ctx._named_project("сколько уже ушло денег на 12")) == 12
    assert asyncio.run(ctx._named_project("что в 12?")) == 12
    assert asyncio.run(ctx._named_project("сколько ушло на 99")) is None
    assert asyncio.run(ctx._named_project("сколько стоят 12 картинок")) is None
