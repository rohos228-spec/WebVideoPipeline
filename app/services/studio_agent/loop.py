"""Петля разговора: модель просит инструмент, мы выполняем, она объясняет.

**Почему не нативный tool-calling.** Текстовый клиент проекта
(`app/services/gpt_api.py::chat`) параметра `tools` не имеет, а провайдер, на
котором держится 82% себестоимости, не поддерживает даже structured outputs.
Строить разговор на функции, которой у половины провайдеров нет, значит
привязать продукт к одному поставщику ровно там, где §12 требует
обратного — заранее проверенной замены.

Поэтому контракт тот же, на котором работает весь остальной конвейер: модель
отвечает JSON-объектом, объект разбирается каноническим экстрактором
(`app/contracts/extract.py`, он умеет и голый JSON, и ```json-фенсы, и
сбалансированный объект внутри болтовни — последнее важно для моделей,
которые пишут `<think>` прямо в content).

Формат ответа ровно один из двух::

    {"say": "текст человеку"}
    {"tool": "estimateStep", "args": {"project_id": 1, "step_code": "video"}}

**Петля конечна.** Ограничение по числу шагов — не защита от зацикливания, а
защита от денег: каждый виток это платный вызов модели, и модель, зациклившая
на `showStoryboard`, потратит баланс, ничего не сделав.

**Ошибка инструмента возвращается модели, а не пользователю.** Текст
`ToolError` написан так, чтобы по нему можно было исправиться: «шага такого
нет, доступны такие-то». Пользователю нужен ответ, а не трассировка.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from loguru import logger

from app.services.studio_agent.tools import ToolError, call_tool, tool_manifest

#: Сколько витков «модель → инструмент → модель» разрешено на одно сообщение.
#: Восьми хватает на самый длинный осмысленный сценарий (смета → показать →
#: подтвердить → запустить), а зациклившаяся модель остановится, потратив
#: предсказуемую сумму.
MAX_TURNS: int = 8

SYSTEM_PROMPT = """\
Ты ведёшь человека по конвейеру создания видеоролика. Отвечай по-русски.

ГЛАВНОЕ ПРАВИЛО: ты дирижёр, а не автор конвейера. Порядок шагов задан
системой. Ты не решаешь, что делать дальше по своему разумению — ты
вызываешь существующие шаги и объясняешь человеку, что происходит и сколько
это стоит. Если шаг сейчас запустить нельзя, система откажет; не пытайся
обойти отказ другим шагом.

ПРО ДЕНЬГИ. У каждого шага есть цена в кредитах, и человек обязан видеть её
ДО запуска, а не после списания. Всё, что дороже 1 кредита, требует явного
согласия человека: сначала назови цену и спроси, потом запускай с
confirm=true. Перерисовать один кадр стоит копейки, переделать раскадровку —
в тысячу раз дороже: это разные действия, и говорить о них надо по-разному.
Итерации веди на картинках, видео покупается один раз в конце.

ФОРМАТ ОТВЕТА. Отвечай ТОЛЬКО JSON-объектом, без пояснений вокруг.
Либо позвать инструмент:
{"tool": "имя", "args": {...}}
Либо сказать человеку:
{"say": "текст"}

Доступные инструменты:
%s
"""


@dataclass
class AgentEvent:
    """Что произошло на витке. Из этого чат рисует ленту."""

    type: str  # tool_call | tool_result | tool_error | message | limit
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentTurn:
    """Итог обработки одного сообщения человека."""

    reply: str = ""
    events: list[AgentEvent] = field(default_factory=list)


def build_system_prompt() -> str:
    """Системный промт с описанием инструментов из единственного реестра."""
    lines = []
    for tool in tool_manifest():
        args = json.dumps(tool["args"], ensure_ascii=False)
        lines.append(f"- {tool['name']}: {tool['description']}\n  аргументы: {args}")
    return SYSTEM_PROMPT % "\n".join(lines)


async def run_turn(
    session: Any,
    message: str,
    *,
    history: list[dict[str, str]] | None = None,
    ask: Any = None,
    max_turns: int = MAX_TURNS,
) -> AsyncIterator[AgentEvent]:
    """Обработать сообщение человека, отдавая события по мере готовности.

    ``ask`` — как спросить модель: ``async (prompt, system, history) -> str``.
    Вынесен параметром не ради красоты: он позволяет проверять петлю без
    платного вызова, а это единственный способ протестировать её вообще.
    """
    asker = ask or _default_ask
    system = build_system_prompt()
    dialogue: list[dict[str, str]] = list(history or [])
    prompt = message

    for turn in range(max_turns):
        raw = await asker(prompt, system, dialogue)
        action = _parse_action(raw)

        if "say" in action:
            yield AgentEvent("message", {"text": str(action["say"])})
            return

        name = str(action.get("tool") or "")
        raw_args = action.get("args")
        args: dict[str, Any] = dict(raw_args) if isinstance(raw_args, dict) else {}
        yield AgentEvent("tool_call", {"tool": name, "args": args})

        observation: dict[str, Any]
        try:
            result = await call_tool(session, name, args)
        except ToolError as exc:
            yield AgentEvent("tool_error", {"tool": name, "error": str(exc)})
            # Ошибка уходит модели как наблюдение: пусть исправится сама.
            observation = {"tool": name, "error": str(exc)}
        else:
            yield AgentEvent("tool_result", {"tool": name, "result": result})
            observation = {"tool": name, "result": result}

        dialogue = dialogue + [
            {"role": "assistant", "content": json.dumps(action, ensure_ascii=False)},
            {"role": "user", "content": json.dumps(observation, ensure_ascii=False)},
        ]
        prompt = (
            "Это результат вызова инструмента. Продолжай: либо позови следующий "
            "инструмент, либо ответь человеку через say."
        )
        logger.debug("агент: виток {}/{} инструмент {}", turn + 1, max_turns, name)

    # Витки кончились. Молчать здесь нельзя: человек ждёт ответа, а денег
    # потрачено уже на восемь вызовов модели.
    yield AgentEvent(
        "limit",
        {
            "text": (
                "Не уложился в отведённые шаги. Скажи, что именно нужно сделать, "
                "и я выполню это одним действием."
            )
        },
    )


async def collect_turn(
    session: Any,
    message: str,
    *,
    history: list[dict[str, str]] | None = None,
    ask: Any = None,
) -> AgentTurn:
    """Тот же виток, но собранный целиком. Удобно тестам и не-SSE клиентам."""
    result = AgentTurn()
    async for event in run_turn(session, message, history=history, ask=ask):
        result.events.append(event)
        if event.type in ("message", "limit"):
            result.reply = str(event.payload.get("text") or "")
    return result


def _parse_action(raw: str) -> dict[str, Any]:
    """Разобрать ответ модели. Что угодно непонятное — это `say`.

    Модель, ответившая прозой вместо JSON, чаще всего просто ответила
    человеку. Ронять на этом разговор значит наказывать пользователя за
    манеру провайдера; отдать прозу как реплику — ровно то, чего он ждал.
    """
    from app.contracts.errors import LlmContractError
    from app.contracts.extract import extract_json_payload

    try:
        data = extract_json_payload(raw, contract="studio_agent")
    except LlmContractError:
        return {"say": (raw or "").strip() or "Не понял, повтори иначе."}
    if "tool" in data or "say" in data:
        return data
    return {"say": (raw or "").strip()}


async def _default_ask(prompt: str, system: str, history: list[dict[str, str]]) -> str:
    from app.services.gpt_api import chat

    result = await chat(
        prompt=prompt,
        system=system,
        history=history or None,
        auto_pack=False,
        temperature=0.2,
    )
    return str(result.text or "")
