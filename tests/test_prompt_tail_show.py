"""«покажи конец промта первой работы с гпт в проекте 7» отвечало «Не понял, что сделать»:
node.describe умел показать только первые 1500 знаков. Теперь prompt_part=end — конец промта."""

from __future__ import annotations

from typing import Any

import pytest

from app.orchestrator.agents import summarizer as sm
from app.services.action_layer import builtin_node_edit as ne

_LINES = [f"Строка {i}: правило про кадр и свет." for i in range(1, 301)]
_PROMPT = "\n".join(_LINES) + "\nПиши коротко."


class _Ctx:
    def __init__(self, base: dict[str, Any]) -> None:
        self.base = base

    async def call(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        assert (method, path) == ("GET", "/change-control/prompt-base")
        return self.base


def _base(text: str = _PROMPT, **extra: Any) -> dict[str, Any]:
    return {"source": "variant", "text": text, "text_chars": len(text), "variant": "cinematic", **extra}


@pytest.mark.asyncio
async def test_prompt_end_shows_last_lines_from_a_line_start() -> None:
    params = {"project_id": 7, "node_key": "n_excel_gpt_1", "prompt_part": "end"}
    text = await ne._prompt_excerpt(_Ctx(_base()), params)
    head, _, rest = text.partition("\n")
    assert head == "Текст промта «cinematic»:"
    body, _, note = rest.rpartition("\n")
    assert body.endswith("Пиши коротко.")
    assert body.startswith("Строка ") and body.split("\n")[0] in _LINES
    assert len(body) <= ne.PROMPT_SHOWN_CHARS
    assert note == f"(показаны последние {len(body)} из {len(_PROMPT)} знаков)"


@pytest.mark.asyncio
async def test_prompt_start_is_still_the_default() -> None:
    text = await ne._prompt_excerpt(_Ctx(_base()), {"project_id": 7, "node_key": "n_excel_gpt_1"})
    assert "\nСтрока 1: правило" in text and "Пиши коротко." not in text
    assert "(показаны первые 1500 из" in text
    short = await ne._prompt_excerpt(
        _Ctx(_base("Короткий промт без хвоста.")), {"project_id": 7, "node_key": "n", "prompt_part": "end"}
    )
    assert short == "Текст промта «cinematic»:\nКороткий промт без хвоста."


@pytest.mark.asyncio
async def test_truncated_base_never_claims_to_show_the_end() -> None:
    text = await ne._prompt_excerpt(
        _Ctx(_base(truncated=True)), {"project_id": 7, "node_key": "n", "prompt_part": "end"}
    )
    assert "последние" not in text and "(показаны первые" in text


def test_describe_schema_accepts_prompt_part_only_start_or_end() -> None:
    from app.services.action_layer.spec import ensure_builtin_actions, registry

    ensure_builtin_actions()
    schema = registry.get("node.describe").params_schema
    assert schema["properties"]["prompt_part"]["enum"] == ["start", "end"]
    assert schema["required"] == ["project_id", "node_key"]


def test_summarizer_quotes_the_tail_and_keeps_its_note() -> None:
    body = "\n".join(_LINES[-40:]) + "\nПиши коротко."
    note_text = (
        "Нода «Работа с GPT 1».\n"
        f"Текст промта «cinematic»:\n{body}\n(показаны последние {len(body)} из 9000 знаков)"
    )
    note = sm.StepNote("node.describe", "Показать настройки ноды", ok=True, text=note_text)
    facts = sm.TaskFacts(
        goal="покажи конец промта первой работы с гпт в проекте 7", status="done", steps=[note]
    )
    (item,) = sm.prompt_excerpts(facts)
    assert item.body == body
    assert item.tail == f"(показаны последние {len(body)} из 9000 знаков)"
    out = sm.ensure_prompt_quoted("Промт ноды длинный.", facts)
    assert "> Пиши коротко." in out and "(показаны последние" in out
    # Длиннее лимита цитаты: режем начало, конец промта остаётся.
    long_body = "\n".join(_LINES) + "\nПиши коротко."
    clipped = sm._clip_excerpt("Текст промта", long_body, "(показаны последние 9 из 9000 знаков)", 9000)
    assert clipped.body.endswith("Пиши коротко.") and len(clipped.body) <= sm._PROMPT_QUOTE_CHARS
    assert clipped.tail.startswith("(показаны последние ")


def test_planner_is_told_about_the_prompt_end() -> None:
    from app.orchestrator.agents.planner import _CATALOG_RULES

    assert any("prompt_part=end" in rule and "конец" in rule for rule in _CATALOG_RULES)
