"""Planner R3: план через router, правила из rag.retrieve в system.

Чужой RAG и роутер не импортируются. В тестах — ScriptedRouter и мок retrieve.
"""

from __future__ import annotations

import inspect
import json
from typing import Any

from pydantic import ValidationError

from app.orchestrator.agents.launch_scope import MAX_PARALLEL
from app.orchestrator.agents.llm_call import router_call
from app.orchestrator.agents.retrieve import retrieve, rules_block
from app.orchestrator.contracts import AgentRole, BudgetScope, Chunk, PlanStep


class NullRetriever:
    async def retrieve(
        self,
        query: str,
        corpora: list[str] | None = None,
        top_k: int = 8,
    ) -> list[Chunk]:
        del query, corpora, top_k
        return []


class RouterPlanner:
    """strong-вызов. Ответ — JSON ``{"steps": [PlanStep, ...]}`` или список."""

    def __init__(
        self,
        router: Any,
        rag: Any | None = None,
        catalog: Any | None = None,
        nodes: Any | None = None,
        undo: Any | None = None,
    ) -> None:
        self.router = router
        self.rag = rag or NullRetriever()
        self.catalog = catalog
        self.nodes = nodes
        self.undo = undo
        self.last_response: Any = None
        self.last_reason = ""
        # Ответ пустой или не JSON: сбой планировщика, а не «просьба не понята».
        self.last_parse_failed = False
        # План собран кодом (откат по журналу), модель не спрашивали.
        self.last_from_code = False

    async def make_plan(
        self,
        goal: str,
        context: str = "",
        *,
        run_id: str | None = None,
        task_id: str | int | None = None,
        agent_role: str | None = None,
        budget_scope: str | None = None,
        reasoning_effort: str | None = None,
    ) -> list[PlanStep]:
        # Поля last_* общего планировщика выставляются все вместе после последнего await
        # своей ветки: иначе сосед, ответивший между ними, оставит свои значения в чужом вызове.
        from app.orchestrator.agents.summarizer import gpt_model_clarify_text

        ask = gpt_model_clarify_text(goal)
        if ask:
            # «Смени модель на gpt»: картинки или ноды GPT — вопрос кодом, модель не нужна.
            self.last_from_code = True
            self.last_response = None
            self.last_reason = ask
            self.last_parse_failed = False
            return []
        undo = await self._undo_plan(goal, task_id)
        if undo is not None:
            # «Верни как было»: последняя правка чата известна из журнала, модель не нужна.
            self.last_from_code = True
            self.last_response = None
            self.last_reason = undo[1]
            self.last_parse_failed = False
            return undo[0]
        report = self._data_report_plan(goal, task_id)
        if report is not None:
            # «Отчёт по данным работы с GPT / какие персонажи»: одно чтение, модель не нужна.
            self.last_from_code = True
            self.last_response = None
            self.last_reason = ""
            self.last_parse_failed = False
            return report
        read = _read_plan(goal)
        if read is not None:
            # «Покажи все проекты», «статус проекта 7»: одно чтение, модель думала над ним минуту.
            self.last_from_code = True
            self.last_response = None
            self.last_reason = ""
            self.last_parse_failed = False
            return read
        block = rules_block(self.rag)
        rules = await retrieve(self.rag, goal or context or "plan", ["rules"], 8)
        catalog = self._catalog_block(goal, context, task_id)
        nodes = await self._nodes_block(goal, context, task_id)
        system = _system_prompt(rules, block, catalog, nodes)
        user = goal if not context else f"{goal}\n\n{context}"
        response = await router_call(
            self.router,
            "strong",
            [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            stream=False,
            run_id=run_id,
            task_id=task_id,
            agent_role=agent_role or AgentRole.planner.value,
            budget_scope=budget_scope or BudgetScope.internal.value,
            reasoning_effort=reasoning_effort,
        )
        self.last_from_code = False
        self.last_response = response
        text = getattr(response, "text", "") or ""
        self.last_reason = plan_reason(text)
        self.last_parse_failed = unreadable_plan(text)
        return parse_plan(text)

    def _catalog_block(self, goal: str, context: str, task_id: Any) -> str:
        if self.catalog is None:
            return ""
        text = goal if not context else f"{goal}\n{context}"
        try:
            return str(self.catalog(text, task_id) or "")
        except Exception:
            return ""

    def _data_report_plan(self, goal: str, task_id: Any) -> list[PlanStep] | None:
        from app.orchestrator.agents.data_report import report_step

        try:
            project_of = getattr(self.catalog, "_project_of", None)
            if project_of is None:
                from app.orchestrator.agents.action_catalog import _task_project as project_of
            step = report_step(goal, project_of(task_id))
        except Exception:
            return None
        return [PlanStep.model_validate(step)] if step is not None else None

    async def _undo_plan(self, goal: str, task_id: Any) -> tuple[list[PlanStep], str] | None:
        if self.undo is None:
            return None
        try:
            found = self.undo(goal, task_id)
            if inspect.isawaitable(found):
                found = await found
        except Exception:
            return None
        if found is None:
            return None
        return [PlanStep.model_validate(step) for step in found.steps], str(found.reason or "")

    async def _nodes_block(self, goal: str, context: str, task_id: Any) -> str:
        if self.nodes is None:
            return ""
        text = goal if not context else f"{goal}\n{context}"
        try:
            found = self.nodes(text, task_id)
            if inspect.isawaitable(found):
                found = await found
        except Exception:
            return ""
        return str(found or "")


def _read_plan(goal: str) -> list[PlanStep] | None:
    from app.orchestrator.agents.read_plan import read_step

    try:
        step = read_step(goal)
    except Exception:
        return None
    return [PlanStep.model_validate(step)] if step is not None else None


_FALSE = frozenset({"false", "no", "0", "нет", ""})


class PlanSchemaError(ValueError):
    pass


def _flag(value: Any) -> Any:
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() not in _FALSE
    return True


def _text(value: Any) -> Any:
    if value is None or isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return str(value)
    return json.dumps(value, ensure_ascii=False)


def _group(value: Any) -> Any:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return str(int(value)) if float(value).is_integer() else str(value)
    if isinstance(value, str):
        return value.strip() or None
    return None


_TEXT_SOURCES = ("text", "edits", "append")


def _blank(value: Any) -> bool:
    if isinstance(value, str):
        return not value.strip()
    return value is None or (isinstance(value, (list, dict)) and not value)


def _one_text_source(params: dict) -> dict:
    """prompt.edit_text: пустые ``edits: []`` / ``text: ""`` рядом с заполненным полем — не правка.

    Модель заполняла все три поля (задача 348: append + пустые edits и text), схема отклоняла
    шаг, а подсказка проверки уходила дальше критику. Пустые поля убираем, только если есть
    заполненное: план без единого поля по-прежнему отклоняет сервер.
    """
    if not any(not _blank(params.get(key)) for key in _TEXT_SOURCES if key in params):
        return params
    return {k: v for k, v in params.items() if not (k in _TEXT_SOURCES and _blank(v))}


def _tolerant(item: Any) -> Any:
    if not isinstance(item, dict):
        return item
    out = dict(item)
    if "parallel_group" in out:
        out["parallel_group"] = _group(out["parallel_group"])
    params = out.get("params")
    if out.get("action") == "step.run_step" and isinstance(params, dict) and "node_key" in params:
        from app.services.action_layer.builtin_launch import NODE_KEY_STEPS

        if params.get("step_code") not in NODE_KEY_STEPS:
            out["params"] = {k: v for k, v in params.items() if k != "node_key"}
    if out.get("action") == "prompt.edit_text" and isinstance(params, dict):
        out["params"] = _one_text_source(params)
    for name in ("paid", "mutating"):
        if name in out:
            flag = _flag(out[name])
            if flag is None:
                out.pop(name)
            else:
                out[name] = flag
    for name in ("expected", "risk"):
        if name in out:
            text = _text(out[name])
            if text is None:
                out.pop(name)
            else:
                out[name] = text
    return out


def _schema_text(exc: ValidationError) -> str:
    parts = []
    for error in exc.errors()[:5]:
        where = ".".join(str(part) for part in error.get("loc") or ())
        parts.append(f"{where}: {error.get('msg')}" if where else str(error.get("msg")))
    return "; ".join(parts)


def _unfenced(text: str) -> str:
    raw = (text or "").strip()
    if raw.startswith("```"):
        lines = [line for line in raw.splitlines() if not line.strip().startswith("```")]
        raw = "\n".join(lines).strip()
    return raw


_REASON_KEYS = ("reason", "why", "explanation", "comment")
_REASON_LIMIT = 300


def plan_reason(text: str) -> str:
    try:
        data = json.loads(_unfenced(text))
    except ValueError:
        return ""
    if not isinstance(data, dict):
        return ""
    for key in _REASON_KEYS:
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return " ".join(value.split())[:_REASON_LIMIT]
    return ""


def unreadable_plan(text: str) -> bool:
    raw = _unfenced(text)
    if not raw:
        return True
    try:
        json.loads(raw)
    except json.JSONDecodeError:
        return True
    return False


def parse_plan(text: str) -> list[PlanStep]:
    raw = _unfenced(text)
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if isinstance(data, dict):
        data = data.get("steps", data.get("plan", []))
    if not isinstance(data, list):
        raise PlanSchemaError("steps должен быть списком шагов")
    steps = []
    for index, item in enumerate(data):
        if not isinstance(item, dict):
            raise PlanSchemaError(f"шаг {index}: ожидался объект с action и params")
        try:
            steps.append(PlanStep.model_validate(_tolerant(item)))
        except ValidationError as exc:
            raise PlanSchemaError(f"шаг {index}: {_schema_text(exc)}") from None
    return steps


def _system_prompt(rules: list[Chunk], block: str = "", catalog: str = "", nodes: str = "") -> str:
    lines = [
        'Составь план как JSON {"steps": [...]} .',
        "Каждый шаг: action, params, expected, paid, mutating, risk, parallel_group, estimated_cost.",
    ]
    if catalog:
        lines.extend(_CATALOG_RULES)
    else:
        lines.append("Перед run_node обязателен шаг verify_code_state (read_pipeline_code + get_node).")
    lines.extend(["Не планируй удаление кадров и сцен.", "Правила пользователя:"])
    text = (block or "").strip()
    if text:
        lines.append(text)
    if not rules and not text:
        lines.append("(нет)")
    for chunk in rules:
        lines.append(chunk.text)
    if catalog:
        lines.append(catalog)
    if nodes:
        lines.append(nodes)
    return "\n".join(lines)


_CATALOG_RULES = (
    "Перед запуском шага (node.run_step, step.run_step) обязателен шаг чтения состояния: "
    "project.status или node.describe.",
    "Перед сменой модели или настроек ноды (node.set_model, node.set_model_costly, node.set_params, "
    "node.set_params_costly) поставь шаг node.describe этой же ноды (тот же project_id и node_key): "
    "бесплатное чтение текущего состояния.",
    "Модель или разрешение картинок и видео всего проекта (настройки проекта) — project.patch_settings, "
    "если новая модель не дороже текущей; дороже, цена неизвестна или не знаешь — "
    "project.patch_settings_costly (подтверждение человека). Модель конкретной ноды — node.set_model.",
    "Одна и та же модель или один промт для одной ноды сразу в нескольких проектах («во всех проектах "
    "10-14 поставь модель X на ноду Y») — один шаг node.set_model_many или node.set_prompt_variant_many "
    "с project_ids всех этих проектов, а не отдельный шаг на каждый проект (вернуть прежнюю модель "
    "в нескольких проектах — один шаг node.revert_model_many); node.describe перед ним "
    "не нужен: действие само проверяет каждый проект и пропускает те, где правка невозможна.",
    "Вопрос о статусе, ошибках, логах или тратах — только шаги чтения (risk none).",
    "Отчёт, статус, логи и описание нод — только шаги чтения, без запуска шагов и без проверок до и после.",
    "Проверку состояния из правила 9 (read_pipeline_code и get_node) здесь выполняет шаг project.status "
    "или node.describe: этих имён в каталоге нет, план из-за них не отменяй.",
    "«Запусти шаг X» или «запусти X» — step.run_step со step_code из списка шагов, перед ним project.status "
    "того же проекта. Нода «Сценарий» и «шаг сценария» — step_code=plan, «закадровый текст» — script, "
    "«разбивка» — split, «объекты» — objects, «предметы» — items, «персонажи» и «герои» — hero, "
    "«озвучка» — audio. Бери step_code с тем же названием из списка шагов, не похожий по смыслу.",
    "Запуск нескольких проектов целиком или до цели без названного шага («запусти 3 4 5», «запусти "
    "проекты 3-12 одновременно», «прогони 5,6,7 до картинок параллельно») — очередь генерации: "
    "queue.enqueue_many с project_ids всех названных проектов, enable_auto_mode=true, mode=full или "
    "mode=until_node с target_node_type («до картинок» — images, «до видео» — videos); если проектов "
    "больше, чем идут одновременно, или сказано «одновременно» / «параллельно» — перед ним "
    f"queue.set_parallel с workers = числу проектов, но не больше {MAX_PARALLEL} (больше схема не "
    f"примет); если окно уже {MAX_PARALLEL}, queue.set_parallel не ставь. step.run_step для этого не ставь.",
    "Запуск названного шага сразу в нескольких проектах («запусти озвучку у проектов 5..9») — сначала "
    "project.status каждого проекта, затем все step.run_step подряд с одной общей parallel_group "
    '(например "launch"), у каждого свой project_id, один проект в группе один раз: такие запуски '
    "идут одновременно. Без общей parallel_group они идут по очереди.",
    "Запуск ноды «Работа с GPT» (n_excel_gpt_…) — step.run_step со step_code=excel_gpt и node_key этой "
    "ноды, не node.run_step: у node.run_step оценка цены есть только для особых нод.",
    "После ноды «Работа с GPT» Студия сама идёт по стрелкам дальше по таким нодам. «Запусти ноду X и дальше "
    "до отчёта / до ноды Y», «по цепочке группы до отчёта», «по одной ноде группы до …» — один step.run_step "
    "(excel_gpt, node_key ноды X) со stop_after_node_key = ключ ноды, на которой остановиться (отчёт группы — "
    "нода с подписью «Отчёт: …» в цепочке ниже). «Только эту ноду» — stop_after_node_key = node_key. "
    "Отдельный step.run_step на каждую ноду цепочки не ставь.",
    "«Запусти следующий шаг» — step.run_step со step_code из строки «следующий шаг по графу» этого проекта.",
    "Подтверждение человека для платных и изменяющих шагов спрашивает карточка после плана: из-за него "
    "план не отменяй и пустой steps не возвращай.",
    "Правка текста промта ноды (дописать требование, заменить фразу, новый текст) — только prompt.edit_text: "
    "append — дописать в конец, edits — замены фрагментов, text — весь новый текст: только одно из трёх "
    "полей, остальные не пиши. change.propose для промтов не используй.",
    "Данные, которые записали ноды «Работа с GPT» (сцены, персонажи, промты картинок и видео по кадрам), "
    "отчёт или выгрузка по ним — report.gpt_data, а не node.output и не report.nodes: один шаг на проект; "
    "node_key — только если названа одна нода, несколько нод одного проекта — один шаг без node_key. "
    "node.output — статус, модель и промт ноды.",
    "«Покажи промт ноды» — node.describe этой ноды; «покажи конец / последние строки / хвост промта» — "
    "node.describe с prompt_part=end.",
    "«Подтверди / одобри сценарий (персонажей, картинки, видео, ролик)» — hitl.approve с kind "
    "(approve_plan, approve_hero, approve_images, approve_videos, approve_final), «отклони …» — "
    "hitl.reject так же; hitl_id ставь, только если человек назвал номер запроса.",
    "«Примени правку промта» — change.apply с proposal_id и expected_digest из списка ожидающих предложений "
    "проекта (самое свежее, если человек не назвал другое). «Отмени предложение правки» (ещё не "
    "применённой) — change.reject с proposal_id оттуда же. Откатить уже применённую правку промта — "
    "change.rollback, не history.rollback.",
    "ceiling_credits ставь, только если человек назвал лимит: в кредитах — как есть, в долларах — "
    "умножь на 200 (1 кредит = $0.005, «лимит $1» = 200). Иначе опусти: для текстовых шагов "
    "сервер подставит потолок по умолчанию и покажет его на карточке подтверждения.",
    "Платный шаг: paid=true, mutating=true, estimated_cost — оценка в долларах; не знаешь цену — 0, "
    "сервер посчитает её сам.",
    "Изменяющий шаг: mutating=true. Подходящего действия нет — верни пустой steps и поле reason: одной "
    "фразой по-русски, почему план пустой и что уточнить.",
    "В params только конкретные значения: ссылки на результаты других шагов (вида $steps[1].id) "
    "план не поддерживает.",
)
