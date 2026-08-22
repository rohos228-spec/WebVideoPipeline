"""Учёт стоимости текстовых LLM-вызовов — таблица llm_calls (этап 3).

Одна строка = один фактический HTTP-вызов (включая ретраи, доборы,
continuation и НЕуспешные). Пишется хуком в самом нижнем HTTP-слое
транспорта (`gpt_api.py`); стоимость логического вызова / ноды /
прогона — всегда агрегат SUM, строк-сумм нет.

Запись — best effort: сбой INSERT не валит платный вызов, но отказ
учёта ВИДИМ (счётчик упавших INSERT) и консервативен для бюджета —
оценочная стоимость незаписанных строк копится in-memory
(`unpersisted_spent`) и входит в spent до конца процесса.

Прайс — `llm_prices.json` рядом с модулем: сырые цены релея за 1M
токенов (НЕ ×3 markup UI из vibecode_models_snapshot). Цена ищется по
served_model (фактическая модель ответа, этап 5), fallback — по
запрошенной; неизвестная модель = цена 0 + WARNING один раз на модель.
"""

from __future__ import annotations

import json
import re
import uuid
from collections import defaultdict
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from functools import lru_cache
from pathlib import Path
from typing import Any

from loguru import logger

from app.db import session_scope
from app.models import LlmCall

_PRICES_PATH = Path(__file__).resolve().parent / "llm_prices.json"

# ── logical_call_id (A.1) ────────────────────────────────────────────────
# Один id на ВНЕШНИЙ chat()/chat_pdf_in_chunks; вложенные слои (adaptive
# 1→2→4, packed parallel, continuation, volume-добор) зовут тот же
# публичный chat() и наследуют id родителя — в т.ч. дочерние задачи
# asyncio.gather (копия контекста). Guard [панель 3/3]: set только если
# contextvar пуст, reset(token) в finally — иначе рекурсия перетёрла бы
# id родителя, а последовательные операции слиплись бы в один.
_logical_call: ContextVar[str | None] = ContextVar("llm_logical_call_id", default=None)


@contextmanager
def logical_call_scope() -> Iterator[str]:
    existing = _logical_call.get()
    if existing is not None:
        yield existing
        return
    token = _logical_call.set(uuid.uuid4().hex)
    try:
        yield _logical_call.get() or ""
    finally:
        _logical_call.reset(token)


def current_logical_call_id() -> str:
    """Для хука записи; вне скоупа — одноразовый id (защитный путь)."""
    return _logical_call.get() or uuid.uuid4().hex


# ── prompt_version_hash (D.1) ────────────────────────────────────────────
# Основной источник — биндинг из call-site'ов, уже считающих хэш для
# чекпоинтов этапа 2 (text_job, scene_design, img_pr, split): учёт и кэш
# несут ОДИН хэш. Fallback — хэш prompt-аргумента внешнего chat()
# (ставится в gpt_api.chat, если ничего не забиндено).
_prompt_hash: ContextVar[str | None] = ContextVar("llm_prompt_version_hash", default=None)


@contextmanager
def bind_prompt_hash(value: str | None, *, fallback: bool = False) -> Iterator[None]:
    """Привязать prompt_version_hash к вложенным LLM-вызовам.

    ``fallback=True`` — ставить только если ничего не забиндено (путь
    gpt_api.chat); явный биндинг call-site'а всегда побеждает.
    """
    if not value or (fallback and _prompt_hash.get() is not None):
        yield
        return
    token = _prompt_hash.set(value)
    try:
        yield
    finally:
        _prompt_hash.reset(token)


def current_prompt_hash() -> str:
    return _prompt_hash.get() or ""


# ── contract_rejected (D.2) ──────────────────────────────────────────────
# Скоуп попытки repair-политики: contextvar держит MUTABLE-коллектор
# (list) — дочерние задачи gather получают копию контекста на тот же
# объект, append'ы видны родителю. record() дописывает id строки, если
# коллектор установлен; set/reset строго в try/finally.
_attempt_rows: ContextVar[list[int] | None] = ContextVar("llm_attempt_rows", default=None)


@contextmanager
def capture_attempt() -> Iterator[list[int]]:
    rows: list[int] = []
    token = _attempt_rows.set(rows)
    try:
        yield rows
    finally:
        _attempt_rows.reset(token)


async def mark_contract_rejected(row_ids: list[int]) -> None:
    """HTTP был успешен, контракт этапа 5 отверг ответ — пометить строки
    попытки. Best effort: сбой UPDATE не валит repair-цикл."""
    ids = [int(i) for i in row_ids if i]
    if not ids:
        return
    try:
        from sqlalchemy import update

        async with session_scope() as session:
            await session.execute(update(LlmCall).where(LlmCall.id.in_(ids)).values(contract_rejected=True))
    except Exception as e:  # noqa: BLE001
        logger.warning("llm_ledger: contract_rejected не записан для {}: {}", ids, e)


# Отказы записи учёта: видимы в API дашборда; стоимость незаписанных
# строк входит в spent бюджет-проверки (project_id=None — adhoc).
_failed_inserts: int = 0
_unpersisted_spent: dict[int | None, float] = defaultdict(float)


def _norm_model(name: str) -> str:
    # Как gpt_api._norm_model_name (не импортируем — цикл): провайдер-
    # префикс шлюза (openai/gpt-5.6-sol) не значим для сравнения.
    tail = (name or "").rsplit("/", 1)[-1]
    return re.sub(r"[^a-z0-9]", "", tail.lower())


@lru_cache(maxsize=1)
def _prices() -> dict[str, tuple[float, float]]:
    """{норм-имя → (input_usd_per_m, output_usd_per_m)}. Смена цен —
    рестарт воркера (осознанно, TTL не нужен)."""
    try:
        doc = json.loads(_PRICES_PATH.read_text(encoding="utf-8"))
        models = doc.get("models") or {}
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("llm_ledger: прайс не прочитан ({}) — все цены 0", e)
        return {}
    out: dict[str, tuple[float, float]] = {}
    for mid, p in models.items():
        try:
            out[_norm_model(mid)] = (
                float(p["input_usd_per_m"]),
                float(p["output_usd_per_m"]),
            )
        except (KeyError, TypeError, ValueError):
            logger.warning("llm_ledger: битая строка прайса для {}", mid)
    return out


_warned_models: set[str] = set()


def price_for(model: str, served_model: str = "") -> tuple[float, float]:
    """(input, output) USD за 1M токенов; приоритет — served_model."""
    prices = _prices()
    for candidate in (served_model, model):
        key = _norm_model(candidate)
        if key and key in prices:
            return prices[key]
    label = served_model or model or "?"
    if label not in _warned_models:
        _warned_models.add(label)
        logger.warning(
            "llm_ledger: модель {} не в прайсе llm_prices.json — цена 0",
            label,
        )
    return (0.0, 0.0)


def _usage_tokens(usage: dict[str, Any] | None) -> tuple[int | None, int | None, int | None]:
    """(prompt, completion, total) из usage обеих веток API.

    chat/completions: prompt_tokens/completion_tokens/total_tokens;
    responses: input_tokens/output_tokens/total_tokens.
    """
    u = usage if isinstance(usage, dict) else {}

    def _pick(*keys: str) -> int | None:
        for k in keys:
            v = u.get(k)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                return int(v)
        return None

    return (
        _pick("prompt_tokens", "input_tokens"),
        _pick("completion_tokens", "output_tokens"),
        _pick("total_tokens"),
    )


def compute_cost(
    usage: dict[str, Any] | None, *, model: str, served_model: str = ""
) -> tuple[float, int | None, int | None, int | None, bool]:
    """(cost_usd, prompt_t, completion_t, total_t, unbilled).

    unbilled — НИ одного токен-поля (обрыв без тела). Частичный usage —
    не unbilled: cost по имеющимся компонентам. Только total без
    разбивки — cost по input-тарифу (консервативно вниз).
    """
    pt, ct, tt = _usage_tokens(usage)
    if pt is None and ct is None and tt is None:
        return (0.0, None, None, None, True)
    in_rate, out_rate = price_for(model, served_model)
    if pt is None and ct is None:
        cost = (tt or 0) * in_rate / 1e6
    else:
        cost = (pt or 0) * in_rate / 1e6 + (ct or 0) * out_rate / 1e6
    return (round(cost, 8), pt, ct, tt, False)


async def record(
    *,
    project_id: int | None,
    node_key: str,
    logical_call_id: str,
    model: str,
    served_model: str = "",
    relay: str = "",
    endpoint: str = "chat",
    usage: dict[str, Any] | None = None,
    result: str = "ok",
    error_kind: str = "",
    prompt_version_hash: str = "",
    response_id: str = "",
    duration_ms: int = 0,
) -> int | None:
    """Записать один фактический HTTP-вызов. Best effort: сбой INSERT —
    WARNING + счётчики (вызов не валится). Возвращает id строки."""
    global _failed_inserts
    cost, pt, ct, tt, unbilled = compute_cost(usage, model=model, served_model=served_model)
    if not prompt_version_hash:
        prompt_version_hash = current_prompt_hash()
    try:
        async with session_scope() as session:
            row = LlmCall(
                project_id=project_id,
                node_key=node_key or "adhoc",
                logical_call_id=logical_call_id,
                model=model or "",
                served_model=served_model or "",
                relay=relay or "",
                endpoint=endpoint or "chat",
                prompt_tokens=pt,
                completion_tokens=ct,
                total_tokens=tt,
                cost_usd=cost,
                result=result,
                error_kind=(error_kind or "")[:60],
                unbilled=unbilled,
                prompt_version_hash=prompt_version_hash or "",
                response_id=(response_id or "")[:120],
                duration_ms=int(duration_ms),
            )
            session.add(row)
            await session.flush()
            row_id = row.id
        collector = _attempt_rows.get()
        if collector is not None and row_id:
            collector.append(int(row_id))
        _bump_spent(project_id, cost)
    except Exception as e:  # noqa: BLE001 — учёт не валит платный вызов
        _failed_inserts += 1
        _unpersisted_spent[project_id] += cost
        logger.warning(
            "llm_ledger: INSERT llm_calls упал (#{} всего): {} — unpersisted_spent[{}]={:.4f}$",
            _failed_inserts,
            e,
            project_id,
            _unpersisted_spent[project_id],
        )
        return None
    return row_id


def failed_insert_count() -> int:
    """Ненулевой = данные учёта неполные (отдаётся в API дашборда)."""
    return _failed_inserts


def unpersisted_spent(project_id: int | None) -> float:
    """Оценочная стоимость строк, не доехавших до БД (входит в spent)."""
    return _unpersisted_spent.get(project_id, 0.0)


# ── Бюджет-предохранитель (E.2) ──────────────────────────────────────────
# Проверка ДО платного вызова (вход chat()) и перед каждой retry-попыткой.
# spent = SUM(cost_usd) по project_id из БД — кэш с TTL (чужие процессы)
# + собственные записи (инкремент на record) + unpersisted_spent.
# Допустимый перерасход параллельных задач одного вызова — N_parallel ×
# цена вызова (зафиксировано в proposal); атомарная резервация — роадмап.

BUDGET_CODE = "budget_exhausted"
_SPENT_TTL_S = 10.0
_spent_cache: dict[int, tuple[float, float]] = {}  # project_id → (spent, ts)
_budget_cache: dict[int, tuple[float | None, float]] = {}  # → (override, ts)


class BudgetExhausted(Exception):
    """Бюджет прогона исчерпан — не-retryable; шаг обязан уйти в паузу.

    Контуры с широким except Exception вокруг LLM-вызовов обязаны
    re-raise это исключение (иначе шаг «успешно» доедет с мусором).
    """

    def __init__(self, *, project_id: int, spent_usd: float, budget_usd: float):
        self.project_id = project_id
        self.spent_usd = spent_usd
        self.budget_usd = budget_usd
        super().__init__(f"бюджет исчерпан: ${spent_usd:.2f} из ${budget_usd:.2f} (проект #{project_id})")


def _now() -> float:
    import time

    return time.monotonic()


async def _sum_spent_db(project_id: int) -> float:
    from sqlalchemy import func, select

    async with session_scope() as session:
        total = (
            await session.execute(
                select(func.coalesce(func.sum(LlmCall.cost_usd), 0.0)).where(LlmCall.project_id == project_id)
            )
        ).scalar_one()
    return float(total or 0.0)


async def spent_usd(project_id: int, *, fresh: bool = False) -> float:
    """Потрачено по проекту: БД (кэш TTL) + незаписанные строки."""
    cached = _spent_cache.get(project_id)
    if fresh or cached is None or _now() - cached[1] > _SPENT_TTL_S:
        try:
            db_sum = await _sum_spent_db(project_id)
        except Exception as e:  # noqa: BLE001 — БД больна: считаем по кэшу
            logger.warning("llm_ledger: SUM llm_calls недоступен: {}", e)
            db_sum = cached[0] if cached else 0.0
        _spent_cache[project_id] = (db_sum, _now())
        cached = _spent_cache[project_id]
    return cached[0] + unpersisted_spent(project_id)


def _bump_spent(project_id: int | None, cost: float) -> None:
    """Собственная запись — инкремент кэша без перечитывания SUM."""
    if project_id is None or cost <= 0:
        return
    cached = _spent_cache.get(project_id)
    if cached is not None:
        _spent_cache[project_id] = (cached[0] + cost, cached[1])


def budget_from_meta(meta: Any, default: float | None = None) -> float:
    """Бюджет проекта: meta["llm_budget_usd"] (0 = выключен для проекта),
    ключа нет → settings.llm_budget_usd (0 = выключен глобально)."""
    from app.settings import settings

    if isinstance(meta, dict) and "llm_budget_usd" in meta:
        try:
            return max(0.0, float(meta.get("llm_budget_usd") or 0.0))
        except (TypeError, ValueError):
            pass
    if default is not None:
        return float(default)
    return float(getattr(settings, "llm_budget_usd", 0.0) or 0.0)


async def budget_usd(project_id: int, *, fresh: bool = False) -> float:
    cached = _budget_cache.get(project_id)
    if fresh or cached is None or _now() - cached[1] > _SPENT_TTL_S:
        override: float | None = None
        try:
            from sqlalchemy import select

            from app.models import Project

            async with session_scope() as session:
                meta = (
                    await session.execute(select(Project.meta).where(Project.id == project_id))
                ).scalar_one_or_none()
            if isinstance(meta, dict) and "llm_budget_usd" in meta:
                override = budget_from_meta(meta)
        except Exception as e:  # noqa: BLE001
            logger.warning("llm_ledger: meta бюджета недоступна: {}", e)
            override = cached[0] if cached else None
        _budget_cache[project_id] = (override, _now())
        cached = _budget_cache[project_id]
    return cached[0] if cached[0] is not None else budget_from_meta(None)


def invalidate_budget_cache(project_id: int) -> None:
    _budget_cache.pop(project_id, None)
    _spent_cache.pop(project_id, None)


async def check_budget(project_id: int | None) -> None:
    """Raise BudgetExhausted, если spent ≥ budget (budget 0 = выключен)."""
    if project_id is None:
        return
    budget = await budget_usd(project_id)
    if budget <= 0:
        return
    spent = await spent_usd(project_id)
    if spent >= budget:
        raise BudgetExhausted(project_id=project_id, spent_usd=spent, budget_usd=budget)
