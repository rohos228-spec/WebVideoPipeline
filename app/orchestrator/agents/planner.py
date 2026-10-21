"""Planner R3: план через router, правила из rag.retrieve в system.

Чужой RAG и роутер не импортируются. В тестах — ScriptedRouter и мок retrieve.
"""

from __future__ import annotations

import inspect
import json
from typing import Any

from pydantic import ValidationError

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
    ) -> None:
        self.router = router
        self.rag = rag or NullRetriever()
        self.catalog = catalog
        self.nodes = nodes
        self.last_response: Any = None
        self.last_reason = ""

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
        self.last_response = response
        text = getattr(response, "text", "") or ""
        self.last_reason = plan_reason(text)
        return parse_plan(text)

    def _catalog_block(self, goal: str, context: str, task_id: Any) -> str:
        if self.catalog is None:
            return ""
        text = goal if not context else f"{goal}\n{context}"
        try:
            return str(self.catalog(text, task_id) or "")
        except Exception:
            return ""

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
        "Составь план как JSON {\"steps\": [...]} .",
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
    "Вопрос о статусе, ошибках, логах или тратах — только шаги чтения (risk none).",
    "Отчёт, статус, логи и описание нод — только шаги чтения, без запуска шагов и без проверок до и после.",
    "Проверку состояния из правила 9 (read_pipeline_code и get_node) здесь выполняет шаг project.status "
    "или node.describe: этих имён в каталоге нет, план из-за них не отменяй.",
    "«Запусти шаг X» или «запусти X» — step.run_step со step_code из списка шагов, перед ним project.status "
    "того же проекта. Нода «Сценарий» и «шаг сценария» — step_code=plan, «закадровый текст» — script, "
    "«разбивка» — split, «объекты» — objects, «предметы» — items, «персонажи» и «герои» — hero, "
    "«озвучка» — audio. Бери step_code с тем же названием из списка шагов, не похожий по смыслу.",
    "Запуск шага сразу в нескольких проектах («у проектов 5..9», «во всех этих проектах») — сначала "
    "project.status каждого проекта, затем все step.run_step подряд с одной общей parallel_group "
    "(например \"launch\"), у каждого свой project_id, один проект в группе один раз: такие запуски "
    "идут одновременно. Без общей parallel_group они идут по очереди.",
    "Запуск ноды «Работа с GPT» (n_excel_gpt_…) — step.run_step со step_code=excel_gpt и node_key этой "
    "ноды, не node.run_step: у node.run_step оценка цены есть только для особых нод.",
    "Правка текста промта ноды (дописать требование, заменить фразу, новый текст) — только prompt.edit_text: "
    "append — дописать в конец, edits — замены фрагментов, text — весь новый текст. change.propose для "
    "промтов не используй.",
    "«Подтверди / одобри сценарий (персонажей, картинки, видео, ролик)» — hitl.approve с kind "
    "(approve_plan, approve_hero, approve_images, approve_videos, approve_final), «отклони …» — "
    "hitl.reject так же; hitl_id ставь, только если человек назвал номер запроса.",
    "«Примени правку промта» — change.apply с proposal_id и expected_digest из списка ожидающих предложений "
    "проекта (самое свежее, если человек не назвал другое).",
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


