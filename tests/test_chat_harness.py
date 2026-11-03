"""Обвязка чата GPT Studio: дата, инструменты по JSON, поток без служебного JSON, защита ссылок."""

import asyncio
from datetime import UTC, datetime

from app.services import chat_harness as h


class _FakeGpt:
    """Отвечает по очереди заготовками и, как настоящий клиент, стримит их кусками."""

    def __init__(self, replies: list[str]) -> None:
        self.replies = list(replies)
        self.prompts: list[str] = []
        self.systems: list[str] = []

    async def ask_with_files(self, prompt, files, *, history, system, on_delta=None, **kw):
        self.prompts.append(prompt)
        self.systems.append(system)
        reply = self.replies.pop(0)
        if on_delta:
            for i in range(0, len(reply), 5):
                await on_delta(reply[i : i + 5])
        return reply


def _run(gpt, monkeypatch, tool_result="НОВОСТЬ: вышла модель X | 1 ноября"):
    async def fake_news(args):
        return tool_result

    monkeypatch.setitem(h.TOOLS, "news", (fake_news, "Ищу свежие новости…"))
    shown: list[str] = []
    phases: list[str] = []

    async def on_delta(d):
        shown.append(d)

    async def on_phase(p):
        phases.append(p)

    reply, calls = asyncio.run(
        h.ask_with_tools(gpt, "что нового?", [], history=[], system="S", on_delta=on_delta, on_phase=on_phase)
    )
    return reply, calls, "".join(shown), phases


def test_date_lines_are_moscow_time():
    lines = h.date_lines(datetime(2026, 11, 2, 21, 30, tzinfo=UTC))
    assert lines[0].startswith("СЕЙЧАС: 3 ноября 2026, вторник, 00:30")
    assert "Эта неделя: 2 ноября 2026 — 8 ноября 2026" in lines[1]


def test_tool_call_runs_and_json_never_shown(monkeypatch):
    gpt = _FakeGpt(['{"tool":"news","args":{"query":"видео нейросети"}}', "Вышла модель X (1 ноября)."])
    reply, calls, shown, phases = _run(gpt, monkeypatch)
    assert reply == "Вышла модель X (1 ноября)."
    assert [c["tool"] for c in calls] == ["news"]
    assert shown == reply  # человек видит только итог
    assert phases == ["Ищу свежие новости…"]
    assert "РЕЗУЛЬТАТ ИНСТРУМЕНТА news" in gpt.prompts[1] and "вышла модель X" in gpt.prompts[1]


def test_tool_call_after_text_is_still_a_call(monkeypatch):
    gpt = _FakeGpt(['Проверяю новости. {"tool":"news","args":{"query":"x"}}', "Итог."])
    reply, calls, shown, _ = _run(gpt, monkeypatch)
    assert reply == "Итог." and len(calls) == 1
    assert '"tool"' not in shown


def test_call_then_self_answer_is_discarded(monkeypatch):
    """Живой случай: модель пишет JSON вызова и тут же «сама отвечает» — ответ берём после результата."""
    gpt = _FakeGpt(
        ['{"tool":"news","args":{"query":"Kling"}}Kling AI — это выдумка модели.', "По новостям: X."]
    )
    reply, calls, shown, _ = _run(gpt, monkeypatch)
    assert reply == "По новостям: X." and len(calls) == 1
    assert shown == "По новостям: X."


def test_broken_json_call_still_recognized():
    # Живой случай: закрывающей скобки нет, сразу текст.
    assert h.parse_tool_call('{"tool":"wiki","args":{"query":"Kling AI"}Kling AI — платформа…') == (
        "wiki",
        {"query": "Kling AI"},
    )


def test_project_tool_text_uses_canvas_titles():
    meta = {
        "canvas_graph": {
            "nodes": [{"id": "n_excel_gpt_1", "type": "excel_gpt", "data": {"label": "Работа с GPT"}}]
        }
    }
    out = h.humanize_reply("Ошибка в n_excel_gpt_1: нет кадров.", h._canvas_keymap(meta))
    assert out == "Ошибка в «Работа с GPT»: нет кадров."


def test_plain_answer_streams_as_is(monkeypatch):
    text = "Обычный ответ с примером {a: 1} внутри."
    reply, calls, shown, _ = _run(_FakeGpt([text]), monkeypatch)
    assert reply == text and shown == text and calls == []


def test_round_limit(monkeypatch):
    call = '{"tool":"news","args":{"query":"x"}}'
    reply, calls, shown, _ = _run(_FakeGpt([call] * (h.MAX_TOOL_ROUNDS + 1)), monkeypatch)
    assert len(calls) == h.MAX_TOOL_ROUNDS
    assert "уточните вопрос" in reply and '"tool"' not in shown


def test_unknown_tool_is_plain_text():
    assert h.parse_tool_call('{"tool":"rm_rf","args":{}}') is None
    assert h.parse_tool_call('```json\n{"tool":"wiki","args":{"query":"ёж"}}\n```') == (
        "wiki",
        {"query": "ёж"},
    )


def test_open_url_blocks_internal_addresses():
    for url in ("http://127.0.0.1:8813/", "http://localhost/admin", "file:///etc/passwd", "http://10.0.0.5/"):
        out = asyncio.run(h.tool_open_url({"url": url}))
        assert out.startswith("Ссылку не открыл"), (url, out)


def test_rules_send_actions_to_project_agent():
    assert "«ИИ-агенту»" in h.HARNESS_RULES and "Сгенерировать" in h.HARNESS_RULES
