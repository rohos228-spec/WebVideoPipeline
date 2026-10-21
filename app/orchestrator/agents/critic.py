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
        self.last_response = None
        self.last_reason = ""
        block = rules_block(self.rag)
        rules = await retrieve(self.rag, context or "critic", ["rules"], 8)
        if plan_deletions(plan):
            return self._decided("reject", "план удаляет кадры или сцены")
        if plan_missing_verify(plan):
            return self._decided("revise", "перед запуском шага нет чтения состояния")
        limit = server_forecast_limit()
        if ceiling_blocks(limit) or forecast_unknown(plan):
            return self._decided("reject", "стоимость плана неизвестна или потолок не даёт запуск")
        if forecast_cost(plan) > limit:
            return self._decided("revise", "прогноз трат выше лимита")
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
        "{\"verdict\": \"approve\" | \"revise\" | \"reject\", \"reason\": \"причина\"}.",
        "Для revise и reject поле reason обязательно: одно короткое предложение по-русски простыми "
        "словами, какое правило нарушено или чего не хватает в плане. Ответ без причины не принимается.",
        "reject — удаление кадров или сцен, либо нарушение правила пользователя, либо запуск "
        "картинок, видео или промтов к ним, когда в проекте нет кадров.",
        "revise — перед запуском шага (step.run_step, node.run_step) нет чтения состояния "
        "(project.status, node.describe или verify_code_state) либо прогноз spend выше лимита.",
        "revise — перед сменой модели или настроек ноды (node.set_model, node.set_model_costly, "
        "node.set_params) нет чтения этой ноды: node.describe с тем же node_key.",
        "Смена модели или настроек ноды по прямой просьбе пользователя — его решение, а не нарушение "
        "правила о модели нод: правило задаёт модель по умолчанию. Действие само проверяет, что нода "
        "есть и модель не дороже текущей, и выполняется только после подтверждения человеком.",
        "Проверку состояния из правила 9 (read_pipeline_code и get_node, verify_code_state) в этом "
        "каталоге выполняет шаг project.status или node.describe: если такой шаг стоит перед запуском, "
        "правило соблюдено, не требуй других проверок.",
        "Правка промта prompt.edit_text: достаточно одного из полей append (дописать в конец), edits "
        "(замены) или text (весь текст); отсутствие остальных — не ошибка. Имена и значения "
        "параметров проверяет сервер по схеме действия: не отклоняй план из-за них.",
        "approve — план только из бесплатных шагов чтения (risk none, paid=false, mutating=false): "
        "отчёты, статус, логи, траты, описание нод. Им не нужны ни чтение состояния перед ними, "
        "ни проверка после, ни подтверждение.",
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
        data = json.loads(body[start:end + 1])
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

    logger.warning("critic: нераспознанный вердикт или нет причины, сырой текст: {}",
                   " ".join(redact_text(raw).split())[:300])
    return "escalate", ""


def _parse_verdict(text: str) -> str:
    return read_verdict(text)[0]


def _json_plan(plan: list[PlanStep]) -> str:
    return json.dumps({"steps": [step.model_dump() for step in plan]}, ensure_ascii=False)


RouterCritic = PlanCritic
