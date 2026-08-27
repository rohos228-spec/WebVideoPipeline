"""Петля разговора: модель просит инструмент, мы выполняем, она объясняет.

**Два протокола, одна история.** Разговор с инструментами хранится в одном
каноническом виде — блоками формата Anthropic Messages: реплика ассистента
может нести `text` и `tool_use`, реплика человека — `text` и `tool_result`.
Как это уходит модели, решает протокол:

* *нативный* — Claude через `/v1/messages` (`gpt_api.native_tools_available`):
  схемы инструментов идут параметром `tools`, блоки — как есть. Модель
  отличает реплику человека от ответа инструмента по типу блока, а не по
  догадке; аргументы приходят по схеме, а не из JSON, найденного в прозе;
* *текстовый* — всё остальное: те же блоки рендерятся в текст
  (`{"tool": …, "args": …}` / `{"tool": …, "result": …}`), модель отвечает
  JSON-объектом, объект разбирает канонический экстрактор
  (`app/contracts/extract.py`). Это запасной путь, не основной: §12 требует
  заранее проверенной замены провайдера, и она здесь есть.

**История живёт у клиента, но собирает её сервер.** После каждого шага
петли (виток с инструментами, финальная реплика) уходит событие `history` —
канонические сообщения, добавившиеся с прошлого такого события, всегда
целыми парами `tool_use` ↔ `tool_result`. Ход, оборванный на середине
(«прервать», сеть), теряет только недоделанный хвост, а не запущенный шаг:
модель на следующем ходе будет знать, что `runStep` уже отработал.
Клиент хранит сообщения непрозрачно и присылает обратно; сервер перед
использованием чистит (`sanitize_history`): роли, типы блоков, парность
`tool_use` ↔ `tool_result`. Ни одна таблица для этого не заведена намеренно —
разговор с оркестратором не документ, а рабочий контекст открытой вкладки.

**Модель должна помнить, о чём её спросили.** После инструмента ей уходит не
голое «продолжай», а исходная просьба человека: между вопросом и ответом
лежат килобайты JSON от `showGraph`, и без напоминания модель доисполняет
свой прошлый план вместо того, чтобы отвечать на вопрос. Наблюдения при этом
режутся (`OBSERVATION_LIMIT`) — модели важна суть, а контекст не резиновый.

**Петля конечна.** Ограничение по числу витков — не защита от зацикливания, а
защита от денег: каждый виток это платный вызов модели.

**Ошибка инструмента возвращается модели, а не пользователю.** Текст
`ToolError` написан так, чтобы по нему можно было исправиться.
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

#: Сколько символов результата инструмента уезжает модели. showStages —
#: сотни символов, showGraph и showStoryboard — тысячи и десятки тысяч.
OBSERVATION_LIMIT: int = 6_000

#: Сколько символов исходной просьбы человека повторяется после инструмента.
REQUEST_ECHO_LIMIT: int = 400

#: Сколько сообщений истории берётся в ход. Хвост важнее начала.
HISTORY_LIMIT: int = 40

Message = dict[str, Any]


@dataclass
class ToolCall:
    """Просьба модели вызвать инструмент."""

    id: str
    name: str
    args: dict[str, Any] = field(default_factory=dict)


@dataclass
class ModelReply:
    """Разобранный ответ модели: текст человеку и/или вызовы инструментов."""

    text: str = ""
    calls: list[ToolCall] = field(default_factory=list)


@dataclass
class AgentEvent:
    """Что произошло на витке. Из этого чат рисует ленту."""

    type: str  # tool_call | tool_result | tool_error | message | limit | history
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentTurn:
    """Итог обработки одного сообщения человека."""

    reply: str = ""
    events: list[AgentEvent] = field(default_factory=list)


# ── промт ───────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """\
Ты ведёшь человека по конвейеру создания короткого видеоролика (60–75 секунд,
вертикаль 9:16). Отвечай по-русски, коротко и по делу, без канцелярита.

ЧТО ТАКОЕ КОНВЕЙЕР. Ролик собирается по цепочке стадий:
%(stages)s
Что конвейер умеет: сценарий и закадровый текст пишет модель; текст режется
на кадры; для сквозных героев и предметов делаются референсы (hero_mode:
auto | no_hero | manual); на каждый кадр генерируется картинка, потом из неё
8-секундный клип; голос — один закадровый диктор, синтез ElevenLabs по
закадровому тексту; музыка генерируется (Suno) под ролик; звуки сопровождения
(SFX) расставляются по таймлайну; сборка — FFmpeg; публикация — отдельным
шагом. Отдельный кадр можно перерисовать или переснять (regenerateFrame), у
кадра можно поменять промт картинки (editFramePrompt).
Чего конвейер НЕ умеет — и ты этого не обещаешь: реплики персонажей и диалоги
в озвучке (голос один, закадровый, он читает закадровый текст целиком);
липсинк; выбор конкретного голоса или языка из чата; загрузка своих картинок,
видео или музыки через чат; клипы длиннее 8 секунд на кадр; ручной монтаж.
Если человек просит то, чего нет, — скажи это прямо одной фразой и предложи
ближайшее из того, что есть: выключить озвучку (узел audio) и оставить
музыку; переписать закадровый текст в нужной манере (например, от лица героя);
поменять параметр ролика. Не подменяй просьбу следующим шагом конвейера.
Параметры ролика (setProjectOptions): %(options)s.

ГЛАВНОЕ ПРАВИЛО: ты дирижёр, а не автор конвейера. Порядок исполнения
считает система по графу ролика. Ты вызываешь существующие шаги и стадии и
объясняешь человеку, что происходит и сколько это стоит. Если шаг сейчас
запустить нельзя, система откажет; не пытайся обойти отказ другим шагом.

КОГДА ДЕЙСТВОВАТЬ, А КОГДА НЕТ. Шаг или стадию запускай ТОЛЬКО если человек
в ЭТОМ сообщении попросил сделать, продолжить или запустить. Вопрос,
сомнение, возражение, «а можно иначе?», недовольство результатом — НЕ
разрешение идти дальше: ответь по существу и остановись, даже если следующий
шаг стоит копейки. Дешевизна снимает вопрос о цене, но не заменяет просьбу.
Твой собственный план из прошлой реплики («потом двинемся к разбивке») — не
поручение: пока человек не сказал «давай», ничего не запускай. Если человеку
что-то не нравится — сначала разберись, что менять, а не продолжай конвейер.

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
готово и что дальше. На вопрос, замечание или упрёк отвечай сразу текстом,
инструменты для этого не нужны.

ПРО ЧЕСТНОСТЬ. Название и идея ролика даны в контексте — не придумывай их.
Не пиши «запускаю», «стартую», «сделал», если в этом же ответе не вызвал
runStep/runStage: текст завершает твой ход, и обещанное после него не
произойдёт. Сначала вызови инструмент, потом расскажи о результате.

ПРО ДЕНЬГИ. У каждого шага есть цена в кредитах, и человек обязан видеть её
ДО запуска, а не после списания. Всё, что дороже 1 кредита, требует явного
согласия человека: сначала назови цену и спроси, потом запускай с
confirm=true. Перерисовать один кадр стоит копейки, переделать раскадровку —
в тысячу раз дороже: это разные действия, и говорить о них надо по-разному.
Итерации веди на картинках, видео покупается один раз в конце.
"""

NATIVE_FORMAT_SECTION = """\
ФОРМАТ ОТВЕТА. Инструменты подключены нативно: зови их вызовом, а человеку
отвечай обычным текстом. Не пиши JSON в тексте.
"""

TEXT_FORMAT_SECTION = """\
ФОРМАТ ОТВЕТА. Отвечай ТОЛЬКО JSON-объектом, без пояснений вокруг.
Либо позвать инструмент:
{"tool": "имя", "args": {...}}
Либо сказать человеку:
{"say": "текст"}
Результат инструмента приходит следующим сообщением как {"tool": "имя",
"result": ...} или {"tool": "имя", "error": "..."} — это ответ системы, а не
реплика человека.

Доступные инструменты:
%(tools)s
"""

PROMISE_REMINDER = (
    "Ты пообещал действие («запускаю», «применяю»), но инструмент не вызвал — "
    "текст завершает ход, и обещанное не произойдёт. Либо вызови runStep/runStage/"
    "applyGraph сейчас, либо ответь человеку без обещания."
)


def _stages_sheet() -> str:
    from app.services.pipeline_stages import STAGES

    return "\n".join(f"- {s.id} «{s.label}»: {s.hint}" for s in STAGES)


def _options_sheet() -> str:
    from app.generation_options import (
        ASPECT_RATIOS_BY_ID,
        IMAGE_GENERATORS_BY_ID,
        IMAGE_RESOLUTIONS_BY_ID,
        VIDEO_GENERATORS_BY_ID,
        VIDEO_RESOLUTIONS_BY_ID,
    )

    parts = [
        f"image_generator ({', '.join(IMAGE_GENERATORS_BY_ID)})",
        f"image_resolution ({', '.join(IMAGE_RESOLUTIONS_BY_ID)})",
        f"aspect_ratio ({', '.join(ASPECT_RATIOS_BY_ID)})",
        f"video_generator ({', '.join(VIDEO_GENERATORS_BY_ID)})",
        f"video_resolution ({', '.join(VIDEO_RESOLUTIONS_BY_ID)})",
        "hero_mode (auto | no_hero | manual)",
        "enrich_slots_count (1..5)",
        "auto_mode (true | false)",
    ]
    return "; ".join(parts)


def build_system_prompt(*, native: bool = False, context: str = "") -> str:
    """Системный промт: конвейер, правила, формат, контекст открытого ролика.

    ``native`` — инструменты уходят параметром API, в промте их списка нет.
    Иначе список инструментов и JSON-протокол описываются здесь же.
    """
    head = SYSTEM_PROMPT % {"stages": _stages_sheet(), "options": _options_sheet()}
    if native:
        fmt = NATIVE_FORMAT_SECTION
    else:
        lines = []
        for tool in tool_manifest():
            args = json.dumps(tool["args"], ensure_ascii=False)
            lines.append(f"- {tool['name']}: {tool['description']}\n  аргументы: {args}")
        fmt = TEXT_FORMAT_SECTION % {"tools": "\n".join(lines)}
    parts = [head, fmt]
    if context:
        parts.append(f"КОНТЕКСТ. {context}")
    return "\n".join(parts)


def native_tool_specs() -> list[dict[str, Any]]:
    """Инструменты в формате Messages API — из того же реестра, что и промт."""
    return [
        {"name": t["name"], "description": t["description"], "input_schema": t["args"]}
        for t in tool_manifest()
    ]


# ── история ─────────────────────────────────────────────────────────────────


def _text_block(text: str) -> dict[str, Any]:
    return {"type": "text", "text": text}


def _clip_text(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return f"{text[:limit]}… [обрезано, всего {len(text)} символов]"


def clip_observation(value: Any, limit: int = OBSERVATION_LIMIT) -> str:
    """Результат инструмента → строка для модели, не длиннее лимита."""
    text = json.dumps(value, ensure_ascii=False, default=str)
    return _clip_text(text, limit)


def sanitize_history(raw: list[Message] | None, *, limit: int = HISTORY_LIMIT) -> list[Message]:
    """Привести историю от клиента к каноническому виду.

    Клиент присылает то, что получил в событии `history`, но верить ему на
    слово нельзя: вкладка могла обрезать хвост посередине пары
    `tool_use` ↔ `tool_result`, а API на непарные блоки отвечает 400.
    Оставляются роли user/assistant, блоки text/tool_use/tool_result;
    `tool_use` без результата в следующем сообщении и `tool_result` без
    вызова в предыдущем выбрасываются; пустые сообщения — тоже.
    """
    messages: list[Message] = []
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip().lower()
        if role not in ("user", "assistant"):
            continue
        content = item.get("content")
        if isinstance(content, str):
            text = content.strip()
            if text:
                messages.append({"role": role, "content": _clip_text(text, OBSERVATION_LIMIT * 2)})
            continue
        if not isinstance(content, list):
            continue
        blocks: list[dict[str, Any]] = []
        for part in content:
            block = _sanitize_block(part, role)
            if block is not None:
                blocks.append(block)
        if blocks:
            messages.append({"role": role, "content": blocks})

    messages = messages[-limit:]
    # Парность: tool_use ждёт tool_result сразу следующим сообщением.
    paired: list[Message] = []
    for idx, msg in enumerate(messages):
        content = msg["content"]
        if not isinstance(content, list):
            paired.append(msg)
            continue
        if msg["role"] == "assistant":
            nxt = messages[idx + 1] if idx + 1 < len(messages) else None
            answered = _result_ids(nxt) if nxt is not None else set()
            kept = [b for b in content if b["type"] != "tool_use" or b["id"] in answered]
        else:
            prev = messages[idx - 1] if idx > 0 else None
            asked = _use_ids(prev) if prev is not None else set()
            kept = [b for b in content if b["type"] != "tool_result" or b["tool_use_id"] in asked]
        if kept:
            paired.append({"role": msg["role"], "content": kept})
    return paired


def _sanitize_block(part: Any, role: str) -> dict[str, Any] | None:
    if isinstance(part, str):
        return _text_block(_clip_text(part, OBSERVATION_LIMIT * 2)) if part.strip() else None
    if not isinstance(part, dict):
        return None
    kind = str(part.get("type") or "")
    if kind == "text":
        text = str(part.get("text") or "")
        return _text_block(_clip_text(text, OBSERVATION_LIMIT * 2)) if text.strip() else None
    if kind == "tool_use" and role == "assistant":
        name = str(part.get("name") or "")
        call_id = str(part.get("id") or "")
        if not name or not call_id:
            return None
        raw_input = part.get("input")
        return {
            "type": "tool_use",
            "id": call_id,
            "name": name,
            "input": dict(raw_input) if isinstance(raw_input, dict) else {},
        }
    if kind == "tool_result" and role == "user":
        use_id = str(part.get("tool_use_id") or "")
        if not use_id:
            return None
        content = part.get("content")
        text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False, default=str)
        block: dict[str, Any] = {
            "type": "tool_result",
            "tool_use_id": use_id,
            "content": _clip_text(text, OBSERVATION_LIMIT),
        }
        if part.get("is_error"):
            block["is_error"] = True
        return block
    return None


def _use_ids(msg: Message) -> set[str]:
    content = msg.get("content")
    if msg.get("role") != "assistant" or not isinstance(content, list):
        return set()
    return {str(b.get("id")) for b in content if b.get("type") == "tool_use"}


def _result_ids(msg: Message) -> set[str]:
    content = msg.get("content")
    if msg.get("role") != "user" or not isinstance(content, list):
        return set()
    return {str(b.get("tool_use_id")) for b in content if b.get("type") == "tool_result"}


def render_for_text(messages: list[Message]) -> list[dict[str, str]]:
    """Каноническая история → текстовые реплики для JSON-протокола.

    `tool_use` становится ответом модели `{"tool": …, "args": …}`,
    `tool_result` — наблюдением (его content уже JSON-строка с именем
    инструмента), текстовая реплика ассистента — `{"say": …}`, чтобы история
    показывала модели тот формат, которого от неё ждут.
    """
    out: list[dict[str, str]] = []
    for msg in messages:
        role = str(msg["role"])
        content = msg["content"]
        if isinstance(content, str):
            text = json.dumps({"say": content}, ensure_ascii=False) if role == "assistant" else content
            out.append({"role": role, "content": text})
            continue
        parts: list[str] = []
        for block in content:
            kind = block.get("type")
            if kind == "text":
                text = str(block.get("text") or "")
                parts.append(json.dumps({"say": text}, ensure_ascii=False) if role == "assistant" else text)
            elif kind == "tool_use":
                parts.append(
                    json.dumps({"tool": block["name"], "args": block.get("input") or {}}, ensure_ascii=False)
                )
            elif kind == "tool_result":
                parts.append(str(block.get("content") or ""))
        if parts:
            out.append({"role": role, "content": "\n".join(parts)})
    return out


# ── разбор ответа ───────────────────────────────────────────────────────────


def parse_text_reply(raw: str, *, call_id: str = "call_0") -> ModelReply:
    """Разобрать текстовый ответ модели. Что угодно непонятное — это реплика.

    Модель, ответившая прозой вместо JSON, чаще всего просто ответила
    человеку. Ронять на этом разговор значит наказывать пользователя за
    манеру провайдера; отдать прозу как реплику — ровно то, чего он ждал.
    """
    from app.contracts.errors import LlmContractError
    from app.contracts.extract import extract_json_payload

    try:
        data = extract_json_payload(raw, contract="studio_agent")
    except LlmContractError:
        return ModelReply(text=(raw or "").strip() or "Не понял, повтори иначе.")
    if "tool" in data:
        raw_args = data.get("args")
        args = dict(raw_args) if isinstance(raw_args, dict) else {}
        return ModelReply(calls=[ToolCall(id=call_id, name=str(data.get("tool") or ""), args=args)])
    if "say" in data:
        return ModelReply(text=str(data["say"]))
    return ModelReply(text=(raw or "").strip())


def reply_from_blocks(blocks: list[dict[str, Any]]) -> ModelReply:
    """Блоки ответа Messages → ModelReply."""
    reply = ModelReply()
    texts: list[str] = []
    for block in blocks:
        kind = block.get("type")
        if kind == "text":
            texts.append(str(block.get("text") or ""))
        elif kind == "tool_use":
            raw_input = block.get("input")
            reply.calls.append(
                ToolCall(
                    id=str(block.get("id") or ""),
                    name=str(block.get("name") or ""),
                    args=dict(raw_input) if isinstance(raw_input, dict) else {},
                )
            )
    reply.text = "\n".join(t for t in texts if t.strip()).strip()
    return reply


# ── петля ───────────────────────────────────────────────────────────────────


async def run_turn(
    session: Any,
    message: str,
    *,
    history: list[Message] | None = None,
    ask: Any = None,
    max_turns: int = MAX_TURNS,
    project_id: int | None = None,
    native: bool | None = None,
) -> AsyncIterator[AgentEvent]:
    """Обработать сообщение человека, отдавая события по мере готовности.

    ``ask`` — как спросить модель: ``async (messages, system, tools) -> ModelReply | str``.
    В нативном протоколе ``messages`` — канонические блоки, ``tools`` — схемы;
    в текстовом ``messages`` — реплики-строки, ``tools`` — None, а строка в
    ответе разбирается как JSON-протокол. Параметр вынесен не ради красоты:
    он позволяет проверять петлю без платного вызова.

    ``native`` — какой протокол. По умолчанию определяется по активной
    модели (`gpt_api.native_tools_available`); с подменённым ``ask`` —
    текстовый, если не сказано иначе.

    ``project_id`` — какой ролик открыт у человека. Без него «сделай картинки»
    заставляло бы модель спрашивать номер проекта.
    """
    asker = ask or _default_ask
    if native is None:
        native = _native_by_default() if ask is None else False
    context = await _project_context(session, project_id) if project_id else ""
    system = build_system_prompt(native=native, context=context)
    tools = native_tool_specs() if native else None

    dialogue: list[Message] = sanitize_history(history)
    # Что добавилось с последнего события `history`; уходит клиенту целиком.
    pending: list[Message] = [{"role": "user", "content": message}]
    dialogue = dialogue + pending

    acted = False  # вызывался ли в этом ходе инструмент, который что-то делает
    reminded = False  # напоминание про обещание без действия — один раз на ход
    for turn in range(max_turns):
        raw = await asker(dialogue if native else render_for_text(dialogue), system, tools)
        reply = parse_text_reply(raw, call_id=f"call_{turn}") if isinstance(raw, str) else raw

        if not reply.calls:
            text = reply.text
            if not acted and not reminded and _promises_action(text):
                # Модель написала «запускаю» и на этом остановилась бы: текст
                # завершает ход. Один раз возвращаем ей это как наблюдение —
                # пусть либо вызовет инструмент, либо перепишет без обещания.
                reminded = True
                step = [
                    {"role": "assistant", "content": text},
                    {"role": "user", "content": PROMISE_REMINDER},
                ]
                dialogue = dialogue + step
                pending = pending + step
                logger.debug("агент: виток {}/{} обещание без действия", turn + 1, max_turns)
                continue
            pending.append({"role": "assistant", "content": text})
            yield AgentEvent("message", {"text": text})
            yield AgentEvent("history", {"messages": pending})
            return

        if reply.text:
            # Текст перед инструментами — «смотрю схему»: человеку видно,
            # что модель делает, а не только карточки вызовов.
            yield AgentEvent("message", {"text": reply.text})

        assistant_blocks: list[dict[str, Any]] = []
        if reply.text:
            assistant_blocks.append(_text_block(reply.text))
        result_blocks: list[dict[str, Any]] = []
        for call in reply.calls:
            assistant_blocks.append(
                {"type": "tool_use", "id": call.id, "name": call.name, "input": call.args}
            )
            yield AgentEvent("tool_call", {"id": call.id, "tool": call.name, "args": call.args})
            observation: dict[str, Any]
            is_error = False
            try:
                result = await call_tool(session, call.name, call.args)
            except ToolError as exc:
                yield AgentEvent("tool_error", {"id": call.id, "tool": call.name, "error": str(exc)})
                # Ошибка уходит модели как наблюдение: пусть исправится сама.
                observation = {"tool": call.name, "error": str(exc)}
                is_error = True
            else:
                yield AgentEvent("tool_result", {"id": call.id, "tool": call.name, "result": result})
                observation = {"tool": call.name, "result": result}
                acted = acted or _is_action(call.name)
            block: dict[str, Any] = {
                "type": "tool_result",
                "tool_use_id": call.id,
                "content": clip_observation(observation),
            }
            if is_error:
                block["is_error"] = True
            result_blocks.append(block)
            logger.debug("агент: виток {}/{} инструмент {}", turn + 1, max_turns, call.name)

        result_blocks.append(_text_block(_continuation_note(message)))
        step = [
            {"role": "assistant", "content": assistant_blocks},
            {"role": "user", "content": result_blocks},
        ]
        dialogue = dialogue + step
        pending = pending + step
        yield AgentEvent("history", {"messages": pending})
        pending = []

    # Витки кончились. Молчать здесь нельзя: человек ждёт ответа, а денег
    # потрачено уже на восемь вызовов модели.
    limit_text = (
        "Не уложился в отведённые шаги. Скажи, что именно нужно сделать, и я выполню это одним действием."
    )
    pending.append({"role": "assistant", "content": limit_text})
    yield AgentEvent("limit", {"text": limit_text})
    yield AgentEvent("history", {"messages": pending})


def _continuation_note(message: str) -> str:
    """Что модель видит после результата инструмента — вместе с исходной просьбой."""
    echo = _clip_text(" ".join(message.split()), REQUEST_ECHO_LIMIT)
    return (
        f"Это результаты инструментов. Исходная просьба человека: «{echo}». "
        "Продолжай: либо позови следующий инструмент, либо ответь человеку — "
        "именно на его просьбу."
    )


async def collect_turn(
    session: Any,
    message: str,
    *,
    history: list[Message] | None = None,
    ask: Any = None,
    project_id: int | None = None,
    native: bool | None = None,
) -> AgentTurn:
    """Тот же виток, но собранный целиком. Удобно тестам и не-SSE клиентам."""
    result = AgentTurn()
    async for event in run_turn(
        session, message, history=history, ask=ask, project_id=project_id, native=native
    ):
        result.events.append(event)
        if event.type in ("message", "limit"):
            result.reply = str(event.payload.get("text") or "")
    return result


#: «Запускаю», «стартую» и т.п. в реплике человеку. Слово-обещание само по
#: себе не ошибка — ошибка сказать его, не вызвав инструмент в том же ходе.
_PROMISE_RE = re.compile(r"\b(запуска(ю|ем)|запущу|стартую|стартуем|применяю|применил[аи]?)\b", re.IGNORECASE)


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


def _native_by_default() -> bool:
    from app.services.gpt_api import native_tools_available

    try:
        return bool(native_tools_available())
    except Exception:  # noqa: BLE001 — не смогли определить маршрут: текстовый протокол работает везде
        return False


async def _project_context(session: Any, project_id: int) -> str:
    """Строка контекста об открытом ролике: номер, название, идея.

    Без названия и идеи модель их выдумывает: showStages отдаёт стадии и
    цены, и спросить, о чём ролик, ей было неоткуда.
    """
    head = f"открыт проект #{project_id}; project_id для инструментов — {project_id}"
    try:
        from app.models import Project

        project = await session.get(Project, project_id)
    except Exception:  # noqa: BLE001 — контекст вспомогательный, ход важнее
        project = None
    if project is None:
        return head
    parts = [head]
    if project.title:
        parts.append(f"название: «{project.title}»")
    if project.topic:
        parts.append(f"идея: {str(project.topic)[:600]}")
    return "; ".join(parts)


async def _default_ask(
    messages: list[Message], system: str, tools: list[dict[str, Any]] | None
) -> ModelReply | str:
    """Спросить модель через текстовый клиент проекта — леджер, брейкер, ретраи те же.

    Нативный протокол: вся история, включая последнюю реплику, уходит
    ``history``, ``prompt`` пустой (см. `gpt_api.build_messages`), ответ —
    блоками из ``raw["content"]``. Текстовый: последняя реплика — ``prompt``,
    ответ — строка под JSON-протокол.
    """
    from app.services.gpt_api import chat

    if tools is not None:
        result = await chat(prompt="", system=system, history=messages, tools=tools, auto_pack=False)
        blocks = result.raw.get("content") if isinstance(result.raw, dict) else None
        if isinstance(blocks, list):
            return reply_from_blocks(blocks)
        return ModelReply(text=str(result.text or ""))

    prior = [dict(m) for m in messages[:-1]]
    last = str(messages[-1]["content"]) if messages else ""
    result = await chat(
        prompt=last,
        system=system,
        history=prior or None,
        auto_pack=False,
        temperature=0.2,
    )
    return str(result.text or "")
