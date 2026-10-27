import asyncio

from app.orchestrator.agents import question_context as qc
from app.orchestrator.agents.project_nodes import match_nodes

ROWS = [
    {"key": "n_split", "type": "split", "type_ru": "Разбивка", "label": "Разбивка"},
    {"key": "n_plan", "type": "plan", "type_ru": "Сценарий", "label": "Сценарий"},
]


class _Nodes:
    async def rows(self, project_id):
        return ROWS


def test_role_question_loads_roster_and_describe():
    ctx = qc.QuestionContext(nodes=_Nodes())
    assert asyncio.run(ctx._roster("а что вообще делает разбивка?", 12)) == ROWS
    assert match_nodes(ROWS, "а что вообще делает разбивка?") == ["n_split"]
    seen = []

    async def one(action, params, app):
        seen.append((action, params))
        return qc.Fact(action, action, "ok")

    ctx._one = one
    asyncio.run(ctx.gather("а что вообще делает разбивка?", 12))
    assert ("node.describe", {"project_id": 12, "node_key": "n_split"}) in seen


def test_numbered_gpt_node():
    rows = ROWS + [
        {"key": "n_excel_gpt_1", "type": "excel_gpt", "type_ru": "Работа с GPT", "label": "Работа с GPT"},
        {"key": "n_excel_gpt_2", "type": "excel_gpt", "type_ru": "Работа с GPT", "label": "Работа с GPT"},
    ]

    class Nodes:
        async def rows(self, project_id):
            return rows

    ctx = qc.QuestionContext(nodes=Nodes())
    seen = []

    async def one(action, params, app):
        seen.append((action, params))
        return qc.Fact(action, action, "ok")

    ctx._one = one
    asyncio.run(ctx.gather("зачем нужна работа с gpt 2", 12))
    assert [p["node_key"] for a, p in seen if a == "node.describe"] == ["n_excel_gpt_2"]


def test_route_question_reads_llm_route():
    ctx = qc.QuestionContext(nodes=_Nodes())
    seen = []

    async def one(action, params, app):
        seen.append((action, params))
        return qc.Fact(action, action, "ok")

    ctx._one = one
    asyncio.run(ctx.gather("сценарий в 12 через какой сервис ходит?", 12))
    assert ("node.get_llm_route", {"project_id": 12, "node_key": "n_plan"}) in seen


def test_model_options_question_reads_list_models():
    ctx = qc.QuestionContext(nodes=_Nodes())
    seen = []

    async def one(action, params, app):
        seen.append((action, params))
        return qc.Fact(action, action, "ok")

    ctx._one = one
    asyncio.run(ctx.gather("а для сценария какие еще модели есть, что можно поставить?", 12))
    assert ("node.list_models", {"project_id": 12, "node_key": "n_plan"}) in seen


def test_node_label_without_data_label_uses_type_name():
    from app.services.action_layer.builtin_group import node_label_ru

    meta = {"canvas_graph": {"nodes": [
        {"id": "n_excel_gpt_1", "type": "excel_gpt", "data": {}},
        {"id": "n_excel_gpt_2", "type": "excel_gpt", "data": {}},
    ]}}
    assert node_label_ru(meta, "n_excel_gpt_1") == "Работа с GPT 1"
