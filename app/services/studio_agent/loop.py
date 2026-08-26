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
import re
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

ГЛАВНОЕ ПРАВИЛО: ты дирижёр, а не автор конвейера. Порядок исполнения
считает система по графу ролика. Ты вызываешь существующие шаги и стадии и
объясняешь человеку, что происходит и сколько это стоит. Если шаг сейчас
запустить нельзя, система откажет; не пытайся обойти отказ другим шагом.

ПРО ГРАФ. Форму конвейера ролика (какие узлы есть, что за чем, что
выключено, какая модель на узле) ты можешь ПРЕДЛОЖИТЬ изменить — точечно
(editGraph) или пересобрать целиком (proposeGraph). Предложение никогда не
применяется само: инструмент возвращает разницу и список шагов, которые
сгорят. Перескажи это человеку словами — «добавлю проверку после картинок,
сгорят видео и сборка» — и зови applyGraph с confirm=true только после его
явного согласия. Перед правкой графа всегда смотри showGraph: id узлов
берутся оттуда, а не придумываются. Не удаляй узлы, которых человек не
просил убирать; выключай (set_node disabled=true) вместо удаления, когда
сомневаешься.

С ЧЕГО НАЧИНАТЬ. Если человек просит что-то СДЕЛАТЬ с роликом или
спрашивает, где он сейчас, — сначала showStages: там видно, где проект, что
готово и что дальше. На вопрос, замечание или упрёк отвечай через say
сразу, инструменты для этого не нужны.

ПРО ЧЕСТНОСТЬ. Название и идея ролика даны в контексте сообщения — не
придумывай их. Не пиши «запускаю», «стартую», «сделал», если в этом же
ответе не вызвал runStep/runStage: say завершает твой ход, и обещанное
после него не произойдёт. Сначала вызови инструмент, потом расскажи о
результате.

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
    project_id: int | None = None,
) -> AsyncIterator[AgentEvent]:
    """Обработать сообщение человека, отдавая события по мере готовности.

    ``ask`` — как спросить модель: ``async (prompt, system, history) -> str``.
    Вынесен параметром не ради красоты: он позволяет проверять петлю без
    платного вызова, а это единственный способ протестировать её вообще.

    ``project_id`` — какой ролик открыт у человека. Без него «сделай картинки»
    заставляло бы модель спрашивать номер проекта, который человек видит
    перед собой.
    """
    asker = ask or _default_ask
    system = build_system_prompt()
    dialogue: list[dict[str, str]] = list(history or [])
    prompt = message
    if project_id:
        prompt = f"{await _project_context(session, project_id)}\n{message}"

    acted = False  # вызывался ли в этом ходе инструмент, который что-то делает
    reminded = False  # напоминание про обещание без действия — один раз на ход
    for turn in range(max_turns):
        raw = await asker(prompt, system, dialogue)
        action = _parse_action(raw)

        if "say" in action:
            text = str(action["say"])
            if not acted and not reminded and _promises_action(text):
                # Модель написала «запускаю» и на этом остановилась бы: say
                # завершает ход. Один раз возвращаем ей это как наблюдение —
                # пусть либо вызовет инструмент, либо перепишет без обещания.
                reminded = True
                dialogue = dialogue + [
                    {"role": "assistant", "content": json.dumps(action, ensure_ascii=False)},
                    {"role": "user", "content": PROMISE_REMINDER},
                ]
                prompt = PROMISE_REMINDER
                logger.debug("агент: виток {}/{} обещание без действия", turn + 1, max_turns)
                continue
            yield AgentEvent("message", {"text": text})
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
            acted = acted or _is_action(name)

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
    project_id: int | None = None,
) -> AgentTurn:
    """Тот же виток, но собранный целиком. Удобно тестам и не-SSE клиентам."""
    result = AgentTurn()
    async for event in run_turn(session, message, history=history, ask=ask, project_id=project_id):
        result.events.append(event)
        if event.type in ("message", "limit"):
            result.reply = str(event.payload.get("text") or "")
    return result


#: «Запускаю», «стартую» и т.п. в реплике человеку. Слово-обещание само по
#: себе не ошибка — ошибка сказать его, не вызвав инструмент в том же ходе.
_PROMISE_RE = re.compile(r"\b(запуска(ю|ем)|запущу|стартую|стартуем|применяю|применил[аи]?)\b", re.IGNORECASE)

PROMISE_REMINDER = (
    "Ты пообещал действие («запускаю», «применяю»), но инструмент не вызвал — "
    "say завершает ход, и обещанное не произойдёт. Либо вызови runStep/runStage/"
    "applyGraph сейчас, либо ответь человеку без обещания."
)


def _promises_action(text: str) -> bool:
    return bool(_PROMISE_RE.search(text))


#: Инструменты, которые что-то делают, а не показывают или считают. Предложение
#: графа (editGraph/proposeGraph) сюда не входит: оно не применяется само.
ACTION_TOOLS: frozenset[str] = frozenset(
    {
        "runStep",
        "runStage",
        "applyGraph",
        "discardGraph",
        "resetStep",
        "stopStep",
        "regenerateFrame",
        "editFramePrompt",
        "approveStage",
        "setProjectOptions",
        "createProject",
    }
)


def _is_action(tool_name: str) -> bool:
    return tool_name in ACTION_TOOLS


async def _project_context(session: Any, project_id: int) -> str:
    """Строка контекста об открытом ролике: номер, название, идея.

    Без названия и идеи модель их выдумывает: showStages отдаёт стадии и
    цены, и спросить, о чём ролик, ей было неоткуда.
    """
    head = f"[открыт проект #{project_id}; project_id для инструментов — {project_id}"
    try:
        from app.models import Project

        project = await session.get(Project, project_id)
    except Exception:  # noqa: BLE001 — контекст вспомогательный, ход важнее
        project = None
    if project is None:
        return head + "]"
    parts = [head]
    if project.title:
        parts.append(f"название: «{project.title}»")
    if project.topic:
        parts.append(f"идея: {str(project.topic)[:600]}")
    return "; ".join(parts) + "]"


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
