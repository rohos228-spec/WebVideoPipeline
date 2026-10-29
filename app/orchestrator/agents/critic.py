"""Critic R3: approve | revise | reject.

Сначала детерминированные проверки (удаления, правило 9, прогноз spend),
затем strong-вердикт роутера. Правила из rag.retrieve попадают в system.
≤2 revise считает супервизор и эскалирует reason=critic_escalation.
Без роутера вердикт не approve: отказ, если детерминированные проверки
молчат. Автоодобрение при ``router=None`` — ошибка конфигурации.
"""

from __future__ import annotations

import json
import re
from typing import Any

from loguru import logger

from app.orchestrator.agents.llm_call import router_call
from app.orchestrator.agents.planner import NullRetriever
from app.orchestrator.agents.retrieve import retrieve, rules_block
from app.orchestrator.contracts import (
    STATE_READ_ACTIONS,
    AgentRole,
    BudgetScope,
    Chunk,
    PlanStep,
    ceiling_blocks,
    forecast_cost,
    forecast_unknown,
    plan_deletions,
    plan_missing_verify,
    server_forecast_limit,
)

NO_REASON = "критик не объяснил причину"
# «Подтверждение действия в плане не указано»: подтверждение спрашивает карточка после плана,
# в шагах его нет и быть не должно. Такой revise гонял план по кругу до «критик не одобрил».
_CONFIRM_ONLY = re.compile(
    r"(?:нет|не\s+указан\w*|не\s+предусмотрен\w*|отсутству\w*|без|требует\w*|нужн\w*)\s+(?:\w+\s+){0,2}"
    r"(?:подтвержд|согласи[ея])"
    r"|(?:подтвержд|согласи[ея])\w*[^.;]{0,60}(?:не\s+указан|не\s+предусмотрен|отсутству|нет\b)"
    r"|(?:подтвержд|согласи[ея])\w*\s+(?:\w+\s+)?(?:пользовател|человек|действи|оператор)",
    re.IGNORECASE,
)
# Подтверждение сценария, картинок, видео — шаг конвейера (hitl), а не карточка: такой revise оставляем.
_HITL_WORDS = re.compile(r"сценари|картин|видео|персонаж|геро|ролик|hitl", re.IGNORECASE)
_CONFIRM_RULE = (
    "Подтверждение человека для изменяющих и платных шагов спрашивает карточка после плана: "
    "в шагах плана его нет и не должно быть. Не возвращай revise или reject из-за того, что "
    "в плане не указано подтверждение."
)


# Части причины: «…; …», «…, …», «… и …», «… но …». Каждая часть должна быть про подтверждение.
_PARTS = re.compile(r"[;,.:!?()—–]+|\s-\s|\s(?:и|но|а|а\s+также|также|хотя|причём|притом)\s", re.IGNORECASE)
# Остаток части без слов про подтверждение — только служебные слова, без нового замечания.
_CONFIRM_WORDS = re.compile(
    r"(?<!\w)(?:\w*подтвержд\w*|\w*соглас\w*|не\s+указан\w*|не\s+предусмотрен\w*|отсутству\w*|нет|без|"
    r"требует\w*|нужн\w*|необходим\w*|пользовател\w*|человек\w*|оператор\w*|действи\w*|"
    r"явн\w*|перед|для|в|во|на|по|от|у|к|с|со|не|плане|план|шаг\w*|эт\w*|это|данн\w*|"
    r"изменени\w*|изменяющ\w*|выполнени\w*|запуск\w*|платн\w*|его|её|ее|их|него|них|ни|одн\w*|"
    r"никакого|никакое|какого|либо|-либо|ещё|еще|также|тоже|получен\w*|запрос\w*|ручн\w*)(?!\w)",
    re.IGNORECASE,
)


# «Создание проекта меняет состояние, но подтверждение…»: первая часть только объясняет,
# зачем подтверждение, нового замечания в ней нет.
_WHY_PART = re.compile(
    r"^(?:\w+\s+){0,3}(?:(?:меня|изменя|измен)\w*\s+(?:\w+\s+)?состояни\w*|"
    r"(?:\w+\s+)?платн\w*|тратит\s+деньги)(?:\s+(?:проекта|системы|базы|данных|студии))?$",
    re.IGNORECASE,
)


def _confirm_part(part: str) -> bool:
    if not _CONFIRM_ONLY.search(part):
        return False
    return not re.search(r"\w", _CONFIRM_WORDS.sub(" ", part))


def confirmation_only(reason: str) -> bool:
    """Замечание только о том, что в плане нет подтверждения человека.

    Причина «не тот проект; подтверждение не указано» — не только про подтверждение:
    её оставляем revise, иначе критик пропускает план с настоящей ошибкой.
    """
    text = " ".join((reason or "").split())
    if not text or _HITL_WORDS.search(text):
        return False
    parts = [p.strip() for p in _PARTS.split(text) if p and re.search(r"\w", p)]
    confirm = [p for p in parts if _confirm_part(p)]
    return bool(confirm) and all(p in confirm or _WHY_PART.match(p) for p in parts)


# «Нужно (повторно) прочитать актуальное состояние проекта перед запуском»: задача 334 гоняла
# такой revise по кругу, хотя project.status уже стоял первым шагом плана.
_READ_STATE = re.compile(
    r"прочит|перечит|чтени|статус|состояни|project\.status|node\.describe|verify_code_state", re.IGNORECASE
)
# Белый список: причина целиком из слов «прочитай актуальное состояние проекта перед запуском».
# Любое другое слово (цена, дорого, сотрёт, клиент, тенант, цепочка, английский текст) —
# возможно настоящее замечание, revise остаётся. Чёрный список таких слов всегда неполон.
_READ_WORDS = re.compile(
    r"(?<!\w)(?:\w*прочит\w*|\w*перечит\w*|чтени\w*|читать|читай|статус\w*|состояни\w*|"
    r"актуальн\w*|текущ\w*|свеж\w*|последн\w*|живо\w*|повторн\w*|снова|заново|ещё|еще|раз|сначала|"
    r"перед|прежде|чем|до|непосредственно|сразу|запуск\w*|продолж\w*|возобнов\w*|выполн\w*|"
    r"исполн\w*|следующ\w*|смен\w*|изменени\w*|шаг\w*|проект\w*|нод\w*|план\w*|конвейер\w*|пайплайн\w*|"
    r"нужн\w*|необходим\w*|требует\w*|требуе\w*|следует|надо|долж\w*|сделай|добав\w*|получ\w*|"
    r"провер\w*|убед\w*|узна\w*|и|в|во|на|по|для|с|со|к|у|о|об|его|её|ее|это\w*|эт\w*|"
    r"не|был\w*|бы|чтобы|как)(?!\w)",
    re.IGNORECASE,
)
# Имена шагов чтения, которые критик может назвать в причине.
_READ_NAMES = frozenset({*STATE_READ_ACTIONS, "verify_code_state", "read_pipeline_code", "get_node"})
_LATIN = re.compile(r"[A-Za-z_][\w.]*")
_NUMBER = re.compile(r"\d+")
# Чтение проекта: только эти действия. node.output / node.get_config — не «состояние проекта».
_PROJECT_READS = frozenset({"project.status"})
_NODE_READS = frozenset({"node.describe"})
# Ключи, через которые шаг может указать другой проект, тенанта или цепочку.
_SCOPE_KEYS = re.compile(r"project|tenant|owner|client|until|stop_after|chain|all|many", re.IGNORECASE)


def _changes_state(step: PlanStep) -> bool:
    """Всё, что не чтение по имени действия, считаем изменением: флаги планировщика не доверяем."""
    return bool(step.mutating or step.paid or step.action not in STATE_READ_ACTIONS)


def _same_id(a: Any, b: Any) -> bool:
    return a not in (None, "") and b not in (None, "") and str(a).strip() == str(b).strip()


def _plain_read(read: PlanStep) -> bool:
    """Чтение без флагов изменения и без ключей другого проекта, кроме project_id/node_key."""
    if read.mutating or read.paid:
        return False
    extra = set(read.params or {}) - {"project_id", "node_key"}
    return not any(_SCOPE_KEYS.search(str(key)) for key in extra)


def _read_covers(read: PlanStep, step: PlanStep) -> bool:
    if read.action not in STATE_READ_ACTIONS or not _plain_read(read):
        return False
    params, wanted = read.params or {}, step.params or {}
    # Чтение без project_id не покрывает ничего: чей проект прочитан — неизвестно.
    if not _same_id(params.get("project_id"), wanted.get("project_id")):
        return False
    node = wanted.get("node_key")
    if step.action.startswith("node.") and node:
        # Смена модели или настроек ноды: правило — node.describe этой же ноды.
        return read.action in _NODE_READS and _same_id(params.get("node_key"), node)
    if read.action in _PROJECT_READS:
        return True
    # node.describe покрывает только запуск этой же ноды.
    stop = wanted.get("stop_after_node_key") or node
    return read.action in _NODE_READS and _same_id(params.get("node_key"), stop)


def _plan_tokens(plan: list[PlanStep]) -> tuple[set[str], set[str]]:
    names = {name.lower() for name in _READ_NAMES} | {step.action.lower() for step in plan}
    numbers: set[str] = set()
    for step in plan:
        for key in ("project_id", "node_key"):
            value = (step.params or {}).get(key)
            if value not in (None, ""):
                (numbers if key == "project_id" else names).add(str(value).strip().lower())
    return names, numbers


def _only_read_words(text: str, plan: list[PlanStep]) -> bool:
    names, numbers = _plan_tokens(plan)
    for token in _LATIN.findall(text):
        if token.strip(".").lower() not in names:
            return False
    rest = _LATIN.sub(" ", text)
    if any(number not in numbers for number in _NUMBER.findall(rest)):
        return False
    rest = _NUMBER.sub(" ", rest)
    rest = _READ_WORDS.sub(" ", rest)
    # Остались только знаки препинания: «:», «—», «(», «»».
    return not re.search(r"[^\W_]|\$|%|€|₽", rest)


def read_state_satisfied(plan: list[PlanStep], reason: str) -> bool:
    """Revise «прочитай состояние» по плану, где чтение уже стоит перед каждым изменяющим шагом.

    Шаги плана идут по порядку: чтение раньше изменения и есть «актуальное состояние перед
    запуском». Условия запуска изменяющего шага ещё раз проверяют живое состояние в момент
    исполнения, а подтверждение человека спрашивает карточка: риск шагов не снижается.
    Причина должна быть целиком из слов про чтение состояния, номера — только проектов плана.
    """
    text = " ".join((reason or "").split())
    if not text or not _READ_STATE.search(text) or not _only_read_words(text, plan):
        return False
    changing = [i for i, step in enumerate(plan) if _changes_state(step)]
    if not changing:
        return False
    return all(any(_read_covers(read, plan[i]) for read in plan[:i]) for i in changing)


# «В плане отсутствуют подтверждённые каталогом id и значения параметров из поля v» (задача 348):
# критик пересказал подсказку проверки плана для планировщика. Имена действий и параметры к
# этому моменту уже проверил сервер по схеме (dry-run до критика), а модель их не видит.
_CATALOG_HINT = re.compile(r"каталог|пол[ея]\s+[«\"']?v\b|\bid\b|идентификатор|параметр|схем", re.IGNORECASE)
_CATALOG_WORDS = re.compile(
    r"(?<!\w)(?:\w*каталог\w*|пол[еяю]|v|id|ид|идентификатор\w*|параметр\w*|значени\w*|схем\w*|"
    r"действи\w*|имен\w*|им[яю]|подтвержд\w*|провер\w*|указан\w*|допустим\w*|верн\w*|корректн\w*|"
    r"правильн\w*|отсутству\w*|нет|не|без|только|все|всех|их|ему|его|её|ее|шаг\w*|плане|план\w*|"
    r"нужн\w*|необходим\w*|требует\w*|следует|долж\w*|исправ\w*|использ\w*|бер\w*|взят\w*|"
    r"из|в|во|на|по|для|с|со|к|у|о|и|или|а|но|это\w*|эт\w*|ключ\w*|точн\w*|явн\w*)(?!\w)",
    re.IGNORECASE,
)


def catalog_params_only(reason: str) -> bool:
    """Revise только про «id из каталога / значения из поля v / имена параметров».

    Такие ошибки ловит сервер: план приходит к критику только после проверки схемы
    действий (dry-run). Причина должна быть целиком из этих слов: любое другое замечание
    (проект, цена, удаление, номер, английское имя ключа) оставляет revise.
    """
    text = " ".join((reason or "").split())
    if not text or not _CATALOG_HINT.search(text):
        return False
    rest = _CATALOG_WORDS.sub(" ", text)
    return not re.search(r"[^\W_]|\$|%|€|₽", rest)


def deterministic_verdict(plan: list[PlanStep]) -> tuple[str, str] | None:
    """Проверки критика без модели: удаления, чтение перед запуском, потолок и прогноз трат.

    Супервизор, одобряя план без круга критика, обязан сначала спросить эти проверки:
    иначе «прогноз трат выше лимита» терялся и карточка шла без предупреждения.
    """
    if plan_deletions(plan):
        return "reject", "план удаляет кадры или сцены"
    if plan_missing_verify(plan):
        return "revise", "перед запуском шага нет чтения состояния"
    limit = server_forecast_limit()
    if ceiling_blocks(limit) or forecast_unknown(plan):
        return "reject", "стоимость плана неизвестна или потолок не даёт запуск"
    if forecast_cost(plan) > limit:
        return "revise", "прогноз трат выше лимита"
    return None


class PlanCritic:
    def __init__(self, router: Any, rag: Any | None = None) -> None:
        self.router = router
        self.rag = rag if rag is not None else NullRetriever()
        self.last_response: Any = None
        self.last_reason = ""

    async def review(
        self,
        plan: list[PlanStep],
        context: str = "",
        *,
        run_id: str | None = None,
        task_id: str | int | None = None,
        agent_role: str | None = None,
        budget_scope: str | None = None,
        reasoning_effort: str | None = None,
    ) -> str:
        block = rules_block(self.rag)
        rules = await retrieve(self.rag, context or "critic", ["rules"], 8)
        # Сброс после await: критик общий, сосед мог выставить свои last_* за время retrieve.
        self.last_response = None
        self.last_reason = ""
        found = deterministic_verdict(plan)
        if found is not None:
            return self._decided(*found)
        if self.router is None:
            return self._decided("reject", "проверяющая модель не подключена")
        response = await router_call(
            self.router,
            "strong",
            [
                {"role": "system", "content": _critic_system(rules, block)},
                {
                    "role": "user",
                    "content": _json_plan(plan) + ("\n" + context if context else ""),
                },
            ],
            stream=False,
            run_id=run_id,
            task_id=task_id,
            agent_role=agent_role or AgentRole.critic.value,
            budget_scope=budget_scope or BudgetScope.internal.value,
            reasoning_effort=reasoning_effort,
        )
        self.last_response = response
        text = getattr(response, "text", "") or ""
        verdict, reason = read_verdict(text)
        if verdict == "revise" and confirmation_only(reason):
            # Карточка подтверждения всё равно будет: риск шагов не снижается.
            logger.info("critic revise about confirmation ignored: {}", reason)
            verdict, reason = "approve", "подтверждение спросит карточка"
        elif verdict == "revise" and read_state_satisfied(plan, reason):
            logger.info("critic revise about reading state ignored: read precedes the step: {}", reason)
            verdict, reason = "approve", "чтение состояния уже стоит перед шагом"
        elif verdict == "revise" and catalog_params_only(reason):
            logger.info(
                "critic revise about catalog ids/params ignored: server checked the schema: {}", reason
            )
            verdict, reason = "approve", "имена и параметры действий проверил сервер"
        if verdict == "approve":
            self.last_reason = reason
            return verdict
        if verdict == "escalate":
            return self._decided(verdict, NO_REASON)
        return self._decided(verdict, reason)

    def _decided(self, verdict: str, reason: str) -> str:
        from app.services.plain_ru import plain_ru
        from app.services.redact import redact_text

        self.last_reason = " ".join(plain_ru(redact_text(str(reason or ""))).split())[:300]
        logger.info("critic {}: {}", verdict, self.last_reason)
        return verdict


def _critic_system(rules: list[Chunk], block: str = "") -> str:
    lines = [
        "Ответ — одна строка JSON без текста вокруг: "
        '{"verdict": "approve" | "revise" | "reject", "reason": "причина"}.',
        "Для revise и reject поле reason обязательно: одно короткое предложение по-русски простыми "
        "словами, какое правило нарушено или чего не хватает в плане. Ответ без причины не принимается.",
        "reject — удаление кадров или сцен, либо нарушение правила пользователя, либо запуск "
        "картинок, видео или промтов к ним, когда в проекте нет кадров.",
        "revise — перед запуском шага (step.run_step, node.run_step) нет чтения состояния "
        "(project.status, node.describe или verify_code_state) либо прогноз spend выше лимита.",
        "revise — перед сменой модели или настроек ноды (node.set_model, node.set_model_costly, "
        "node.set_params) нет чтения этой ноды: node.describe с тем же node_key.",
        "node.set_model_many и node.set_prompt_variant_many сами проверяют ноду в каждом проекте: "
        "node.describe перед ними не требуй.",
        "Действия монтажа (montage.*) сами проверяют, что названные кадры есть в проекте, и "
        "отказывают с причиной, если кадра нет: чтение доски или кадров перед ними не требуй.",
        "Смена модели или настроек ноды по прямой просьбе пользователя — его решение, а не нарушение "
        "правила о модели нод: правило задаёт модель по умолчанию. Действие само проверяет, что нода "
        "есть и модель не дороже текущей, и выполняется только после подтверждения человеком.",
        "Проверку состояния из правила 9 (read_pipeline_code и get_node, verify_code_state) в этом "
        "каталоге выполняет шаг project.status или node.describe: если такой шаг стоит перед запуском, "
        "правило соблюдено, не требуй других проверок.",
        "Стоп-ключ only_node из правила 9 в этом каталоге — параметр stop_after_node_key у step.run_step: "
        "равен node_key — только эта нода, другой ключ — цепочка нод «Работа с GPT» по стрелкам до этой "
        "ноды включительно. Если человек сам попросил «дальше до отчёта / до ноды …», такая цепочка — "
        "его решение, а не нарушение правила.",
        "Шаги плана выполняются строго по порядку: project.status или node.describe, стоящий в "
        "плане раньше изменяющего шага, и есть чтение актуального состояния перед ним. Не проси "
        "прочитать состояние ещё раз или «повторно непосредственно перед запуском».",
        "project.continue_pipeline и project.resume сами перепроверяют живое состояние проекта "
        "условиями запуска в момент исполнения и идут через карточку подтверждения.",
        "Правка промта prompt.edit_text: достаточно одного из полей append (дописать в конец), edits "
        "(замены) или text (весь текст); отсутствие остальных — не ошибка. Имена и значения "
        "параметров проверяет сервер по схеме действия: не отклоняй план из-за них.",
        _CONFIRM_RULE,
        "approve — план только из бесплатных шагов чтения (risk none, paid=false, mutating=false): "
        "отчёты, статус, логи, траты, описание нод. Им не нужны ни чтение состояния перед ними, "
        "ни проверка после, ни подтверждение.",
        "approve — бесплатная правка без удаления (paid=false) по прямой просьбе человека: покрытие "
        "или текст кадра, вставка кадра, перестановка шотов на доске монтажа. Подтверждение спросит "
        "карточка, а план не обязан быть только чтением.",
        "montage.merge_scenes, montage.split_scene и montage.explode_scene не удаляют кадры и сцены: "
        "кадры и медиа остаются, меняется только деление на сцены. Это не удаление, предупреждение "
        "показывает карточка. montage.cancel_queue_ops только снимает ещё не сделанную правку из "
        "очереди: файлы не трогает, правку можно поставить заново, это тоже не удаление. "
        "montage.set_scene_chain перезаписывает действия шотов сцены по просьбе человека, это правка, "
        "а не удаление. montage.unlink_ref убирает реф из кадра, файлы остаются.",
        "history.rollback по просьбе «верни как было» возвращает одно прежнее значение из снимка "
        "(текст, настройку, параметр), кадры и сцены он не удаляет; сервер сверяет, что значение "
        "не меняли после правки. Это не удаление, предупреждение показывает карточка.",
        "Правила:",
    ]
    text = (block or "").strip()
    if text:
        lines.append(text)
    if not rules and not text:
        lines.append("(нет)")
    for chunk in rules:
        lines.append(chunk.text)
    return "\n".join(lines)


_VERDICTS = frozenset({"approve", "revise", "reject"})
_LEAD = re.compile(r"^\W*(approve|revise|reject)\b(.*)$", re.IGNORECASE | re.DOTALL)
_LETTER = re.compile(r"[a-zа-яё]", re.IGNORECASE)


def _clean_reason(raw: Any) -> str:
    text = " ".join(str(raw or "").split()).strip(" \"'.,:;—–-")
    if not _LETTER.search(text) or text.lower() in _VERDICTS:
        return ""
    return text


def _json_verdict(raw: str) -> tuple[str, str] | None:
    body = "\n".join(line for line in raw.splitlines() if not line.strip().startswith("```"))
    start, end = body.find("{"), body.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(body[start : end + 1])
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    verdict = str(data.get("verdict") or "").strip().lower()
    if verdict not in _VERDICTS:
        return None
    return verdict, _clean_reason(data.get("reason"))


def read_verdict(text: str) -> tuple[str, str]:
    raw = (text or "").strip()
    found = _json_verdict(raw)
    if found is None:
        match = _LEAD.match(raw)
        if match is not None:
            found = match.group(1).lower(), _clean_reason(match.group(2))
    if found is not None and (found[0] == "approve" or found[1]):
        return found
    from app.services.redact import redact_text

    logger.warning(
        "critic: нераспознанный вердикт или нет причины, сырой текст: {}",
        " ".join(redact_text(raw).split())[:300],
    )
    return "escalate", ""


def _parse_verdict(text: str) -> str:
    return read_verdict(text)[0]


def _json_plan(plan: list[PlanStep]) -> str:
    return json.dumps({"steps": [step.model_dump() for step in plan]}, ensure_ascii=False)


RouterCritic = PlanCritic
