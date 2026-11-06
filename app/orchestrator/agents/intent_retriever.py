"""Стадия намерения: понять свободную фразу и выбрать подходящие действия из ВСЕГО каталога.

Зачем. Каталог для планировщика собирал лексический предфильтр (``planner_catalog.select_catalog``:
стемы, словарь синонимов, ~24 действия). Новая формулировка, разговорный оборот или другой
порядок слов промахивались мимо нужного действия — и планировщик честно отвечал «уточните»
или «не умею», хотя действие есть. Здесь быстрая модель видит компактный список всех
доступных чату действий (id и название) и возвращает строгий JSON:

    {"intent_summary_ru": "перерисовать картинку кадра 2",
     "candidate_action_ids": ["montage.queue_ops", "montage.get_board"],
     "extracted_entities": {"frame_numbers": [2], "project_ids": [], "values": {}},
     "confidence": 0.9,
     "needs_clarification": null}

Контракт со следующей стадией (синтез плана, ``planner.LLMPlanner``):
- ``candidate_action_ids`` добавляются к лексической выборке (она остаётся мягким усилением,
  а не жёстким фильтром) — см. ``action_catalog.boost_task``;
- смысл и данные просьбы уходят планировщику в контекст (:func:`context_note`);
- ``needs_clarification`` модели намерения человеку сразу не показывается: решает планировщик,
  а при пустом плане — ремонт с расширенным каталогом (``supervisor``).

Безопасность не меняется: id вне каталога отбрасываются здесь же, план после планировщика
проходит те же проверки (каталог, схема, критик, карточка подтверждения, бюджет, kill-switch).
Выключатель: ``ORCH_INTENT_RETRIEVER=0`` — прежний путь без этой стадии.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from loguru import logger

TIMEOUT_S = 25.0
MAX_CANDIDATES = 15
_TITLE_CHARS = 70


def enabled() -> bool:
    """Флаг стадии: ``ORCH_INTENT_RETRIEVER`` (по умолчанию включена)."""
    return os.environ.get("ORCH_INTENT_RETRIEVER", "1") != "0"


# Счётчики для логов: доля уточнений, ремонтов и успехов (сбрасываются с процессом).
METRICS: Counter[str] = Counter()


def count(name: str) -> None:
    METRICS[name] += 1
    total = METRICS["tasks"] or 1
    if name in ("plan_ok", "plan_clarify", "repair_used", "repair_ok"):
        logger.info(
            "orch intent metrics: tasks={} ok={} clarify={} ({:.0%}) repair={} repair_ok={}",
            METRICS["tasks"],
            METRICS["plan_ok"],
            METRICS["plan_clarify"],
            METRICS["plan_clarify"] / total,
            METRICS["repair_used"],
            METRICS["repair_ok"],
        )


@dataclass
class Intent:
    """Результат стадии намерения (поля — как в JSON модели, уже проверенные)."""

    summary: str = ""
    candidates: list[str] = field(default_factory=list)
    entities: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0
    clarification: str | None = None
    latency_ms: int = 0

    @property
    def useful(self) -> bool:
        return bool(self.candidates)


_SYSTEM = """Ты — стадия понимания просьб в чате видеостудии. Человек пишет как в мессенджере:
разговорно, с опечатками, сленгом, без номера проекта, в любом порядке слов. Твоя задача —
понять, ЧТО он хочет, и выбрать из каталога действия, которыми это делается.

Верни ТОЛЬКО JSON-объект:
{"intent_summary_ru": "что хочет человек, 3-12 слов, по-деловому",
 "candidate_action_ids": ["id из каталога", ...],
 "extracted_entities": {"frame_numbers": [], "project_ids": [], "values": {}},
 "confidence": 0.0-1.0,
 "needs_clarification": null или "короткий вопрос"}

Правила:
1. candidate_action_ids — только id из каталога ниже, от самого подходящего, 3-12 штук. Добавь и
   чтения, которые нужны перед действием (состояние проекта, доска кадров).
2. Разговорные слова переводи в смысл: «перегень/по новой/заново/переделай» — перегенерация,
   «оживи/анимируй/видос» — видео, «музон/фон» — музыка, «диктор/озвучка/голос» — голос,
   «сабы» — субтитры, «залей/выкладывай» — публикация, «дожми/доделай/до конца» — довести проект.
3. «второй кадр», «на 2м», «кадр2» — frame_numbers [2]. Номера проектов — project_ids.
   В values — прочие значения из просьбы (стиль, язык, длительность, имя голоса, настроение, тема,
   площадка, текст нового промта), дословно.
4. Если открыт проект, просьба без номера проекта относится к нему — это НЕ повод уточнять.
5. needs_clarification — только если без ответа действие нельзя выбрать вообще. Нехватка
   необязательных деталей — не повод: confidence ниже, вопрос null.
6. Вопрос о состоянии, цене, причине сбоя — тоже намерение: выбери чтения.
"""


def catalog_lines(specs: list[Any]) -> str:
    """Компактный каталог для стадии намерения: «id — название [платно]»."""
    rows = []
    for spec in sorted(specs, key=lambda s: s.id):
        title = " ".join(str(getattr(spec, "title_ru", "") or "").split())[:_TITLE_CHARS]
        paid = " [платно]" if getattr(spec, "spends_money", False) else ""
        rows.append(f"{spec.id} — {title}{paid}")
    return "\n".join(rows)


def _parse(text: str, allowed: set[str]) -> Intent | None:
    raw = str(text or "").strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(raw[start : end + 1])
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    ids = data.get("candidate_action_ids")
    picked: list[str] = []
    for item in ids if isinstance(ids, list) else []:
        if isinstance(item, str) and item in allowed and item not in picked:
            picked.append(item)
    try:
        confidence = max(0.0, min(1.0, float(data.get("confidence") or 0.0)))
    except (TypeError, ValueError):
        confidence = 0.0
    entities = data.get("extracted_entities")
    ask = data.get("needs_clarification")
    return Intent(
        summary=" ".join(str(data.get("intent_summary_ru") or "").split())[:200],
        candidates=picked[:MAX_CANDIDATES],
        entities=_clean_entities(entities if isinstance(entities, dict) else {}),
        confidence=confidence,
        clarification=" ".join(ask.split())[:200] if isinstance(ask, str) and ask.strip() else None,
    )


def _ints(value: Any) -> list[int]:
    out = []
    for item in value if isinstance(value, list) else []:
        try:
            num = int(item)
        except (TypeError, ValueError):
            continue
        if 0 < num < 100000 and num not in out:
            out.append(num)
    return out[:20]


def _clean_entities(raw: dict[str, Any]) -> dict[str, Any]:
    values = raw.get("values") if isinstance(raw.get("values"), dict) else {}
    kept = {
        str(k)[:40]: " ".join(str(v).split())[:200]
        for k, v in list(values.items())[:10]
        if isinstance(v, (str, int, float)) and str(v).strip()
    }
    out: dict[str, Any] = {}
    frames, projects = _ints(raw.get("frame_numbers")), _ints(raw.get("project_ids"))
    if frames:
        out["frame_numbers"] = frames
    if projects:
        out["project_ids"] = projects
    if kept:
        out["values"] = kept
    return out


async def retrieve_intent(
    goal: str,
    *,
    router: Any,
    llm_kwargs: dict[str, Any],
    specs: list[Any],
    project_id: int | None = None,
    dialog: str = "",
) -> Intent | None:
    """Стадия намерения для ``goal``; ``None`` — стадия выключена, сбой или долго (идём без неё)."""
    if not enabled() or router is None or not str(goal or "").strip() or not specs:
        return None
    from app.orchestrator.agents.llm_call import router_call
    from app.services.redact import redact_text

    allowed = {spec.id for spec in specs}
    where = f"Открыт проект #{project_id}." if project_id is not None else "Проект не открыт (вся студия)."
    user = "\n".join(
        part
        for part in (
            where,
            f"Недавний разговор:\n{redact_text(dialog)[:1500]}" if dialog.strip() else "",
            f"Просьба человека: {redact_text(str(goal))[:1500]}",
            "Каталог действий:\n" + catalog_lines(specs),
        )
        if part
    )
    started = time.monotonic()
    try:
        response = await asyncio.wait_for(
            router_call(
                router,
                "fast",
                [{"role": "system", "content": _SYSTEM}, {"role": "user", "content": user}],
                stream=False,
                run_id=llm_kwargs.get("run_id"),
                task_id=llm_kwargs.get("task_id"),
                agent_role="classifier",
                budget_scope=llm_kwargs.get("budget_scope"),
            ),
            TIMEOUT_S,
        )
    except Exception as exc:  # noqa: BLE001 — без стадии намерения план строится как раньше
        logger.info("orch intent skipped: {} after {:.1f}s", type(exc).__name__, time.monotonic() - started)
        count("intent_failed")
        return None
    intent = _parse(getattr(response, "text", "") or "", allowed)
    latency = int((time.monotonic() - started) * 1000)
    if intent is None:
        logger.info("orch intent unreadable after {} ms", latency)
        count("intent_unreadable")
        return None
    intent.latency_ms = latency
    count("intent_ok")
    return intent


def context_note(intent: Intent) -> str:
    """Строка контекста для планировщика: смысл просьбы и данные из неё."""
    parts = []
    if intent.summary:
        parts.append(f"Смысл просьбы (стадия понимания): {intent.summary}.")
    if intent.entities:
        parts.append(
            "Данные из просьбы: " + json.dumps(intent.entities, ensure_ascii=False, separators=(",", ":")) + "."
        )
    if intent.candidates:
        parts.append("Подходящие действия, по убыванию: " + ", ".join(intent.candidates) + ".")
    parts.append(
        "Если по смыслу ясно, что делать, верни план, а не вопрос: проект задачи известен, "
        "необязательные параметры опусти."
    )
    return " ".join(parts)


_ASK_WORDS = re.compile(r"уточн|какой|какую|какие|в каком|что именно|\?")


def is_clarify(reason: str) -> bool:
    """Пустой план с вопросом — тупик, который чинит ремонт."""
    return bool(_ASK_WORDS.search(str(reason or "").lower()))
