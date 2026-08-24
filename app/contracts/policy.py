"""Единая repair-retry политика контрактного пути (этап 5, блок D).

По образцу эталона ``ai_result_io.text_job:87-140``, чего эталону не
хватало (карта §4.4): типизация Pydantic, отклонённые ответы на диск,
раздельные лимиты parse-fail / validate-fail.

Схема цикла: вызов LLM → contract.parse (форма) → validate (семантика:
coverage N/N, доменные правила) → успех | LlmContractError → фидбек
«ошибки прошлой попытки» в следующий вызов → исчерпание лимита →
fail-closed raise (наверх до step_failure_policy; никаких тихих None
и частичных результатов — спека «Запрет тихого частичного успеха»).

Repair-попытка НЕ перезапускает адаптивное дробление chat() заново —
политика повторяет только СВОЮ единицу работы (батч/агент-вызов);
транспортные ретраи (gpt_max_retries) живут внутри вызова и repair'ом
не считаются (определение метрики — tasks D.5).
"""

from __future__ import annotations

import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Generic

from loguru import logger

from app.contracts.base import LlmContract, TModel
from app.contracts.errors import LlmContractError

# Retention llm_rejects: последние N файлов на директорию + возраст.
_REJECTS_KEEP = 20
_REJECTS_MAX_AGE_DAYS = 14

FEEDBACK_HEADER = "# ОШИБКИ ПРОШЛОЙ ПОПЫТКИ (исправь и верни ПОЛНЫЙ ответ заново)"


@dataclass
class RepairResult(Generic[TModel]):
    payload: TModel
    reply_text: str
    meta: dict[str, Any] = field(default_factory=dict)
    attempts: int = 1  # всего вызовов LLM в этой единице работы
    parse_fails: int = 0
    validate_fails: int = 0
    rejected_paths: list[Path] = field(default_factory=list)

    @property
    def repairs(self) -> int:
        return self.attempts - 1

    def metrics(self) -> dict[str, int]:
        """Счётчики для NodeRun.meta (метрика приёмки, tasks D.5)."""
        return {
            "attempts": self.attempts,
            "repairs": self.repairs,
            "parse_fails": self.parse_fails,
            "validate_fails": self.validate_fails,
        }


def _prune_rejects(reject_dir: Path) -> None:
    try:
        files = sorted(
            (p for p in reject_dir.glob("*.txt") if p.is_file()),
            key=lambda p: p.stat().st_mtime,
        )
        cutoff = time.time() - _REJECTS_MAX_AGE_DAYS * 86400
        drop = [p for p in files if p.stat().st_mtime < cutoff]
        if len(files) - len(drop) > _REJECTS_KEEP:
            drop.extend(files[: len(files) - _REJECTS_KEEP])
        for p in dict.fromkeys(drop):
            p.unlink(missing_ok=True)
    except OSError:  # retention — best effort, не валить политику
        pass


METRICS_FILE = "llm_metrics.jsonl"


def _write_metrics(
    reject_dir: Path | None,
    *,
    contract: str,
    label: str,
    attempts: int,
    parse_fails: int,
    validate_fails: int,
    ok: bool,
) -> None:
    """Метрика repair-rate (tasks D.5) — JSONL рядом с llm_rejects.

    Одна строка = одна логическая единица работы политики (знаменатель
    метрики). Файл на проект, переживает рестарты и failed-runs; сбор —
    scripts/llm_repair_rate.py. Best effort: сбой записи не валит вызов.
    """
    if reject_dir is None:
        return
    try:
        path = reject_dir.parent / METRICS_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(
                json.dumps(
                    {
                        "ts": datetime.now(UTC).isoformat(timespec="seconds"),
                        "contract": contract,
                        "label": label,
                        "attempts": attempts,
                        "repairs": max(0, attempts - 1),
                        "parse_fails": parse_fails,
                        "validate_fails": validate_fails,
                        "ok": ok,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    except OSError as e:
        logger.warning("contracts/policy: метрика не записана: {}", e)


def _write_reject(
    reject_dir: Path | None,
    *,
    label: str,
    attempt: int,
    reply: str,
    error: LlmContractError,
) -> Path | None:
    if reject_dir is None:
        return None
    try:
        reject_dir.mkdir(parents=True, exist_ok=True)
        safe_label = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in (label or "reply"))
        path = reject_dir / f"{safe_label}_a{attempt}_{error.kind}.txt"
        path.write_text(
            f"# contract: {error.contract or '-'}\n"
            f"# kind: {error.kind}\n"
            f"# error: {error}\n"
            f"# reply_len: {len(reply or '')}\n\n{reply or ''}",
            encoding="utf-8",
        )
        _prune_rejects(reject_dir)
        return path
    except OSError as e:
        logger.warning("contracts/policy: не записал reject на диск: {}", e)
        return None


def _record(contract: str, reply: str, *, label: str, verdict: str, error: str = "") -> None:
    """Сохранить сырой ответ в корпус, если запись включена.

    Пишутся и удачные ответы, и брак. Брак и так падал в `llm_rejects/`, но
    только он: истории успешной работы у конвейера не было вовсе, а голденсет
    из одних поломок проверяет обработку ошибок и ничего не говорит про
    нормальный путь.

    Вызов намеренно ничего не возвращает и не может бросить: рекордер не имеет
    права быть причиной падения шага (см. `app/services/llm_recorder.py`).
    """
    from app.services import llm_recorder

    if not llm_recorder.enabled():
        return
    llm_recorder.record(
        contract=contract,
        reply=reply,
        node_key=label,
        verdict=verdict,
        error=error,
    )


async def run_with_contract(
    *,
    contract: LlmContract[TModel],
    call: Callable[[str | None], Awaitable[str]],
    validate: Callable[[TModel], list[str]] | None = None,
    reject_dir: Path | None = None,
    label: str = "",
    parse_limit: int = 2,
    validate_limit: int = 2,
) -> RepairResult[TModel]:
    """Выполнить единицу работы LLM под контрактом с repair-retry.

    ``call(feedback)`` — вызов модели; при repair получает текст фидбека
    (секция FEEDBACK_HEADER) и обязан включить его в промпт. Схему в
    транспорт (``chat(response_schema=contract.response_schema())``)
    прикрепляет сам вызывающий — политика транспорт-агностична.

    ``validate`` — семантика поверх схемы (coverage N/N после склейки,
    доменные проверки): список проблем, пустой = ок.

    Raises:
        LlmContractError: лимит исчерпан (fail-closed). Транспортные
        ошибки (GptApiError и пр.) пролетают наверх без изменений —
        это не брак формата, их ретраит транспорт/step_failure_policy.
    """
    parse_fails = 0
    validate_fails = 0
    attempts = 0
    rejected: list[Path] = []
    feedback: str | None = None

    # Этап 3: строки llm_calls попытки собираются скоупом учёта; при
    # отказе контракта (HTTP успешен, ответ отвергнут) они помечаются
    # contract_rejected — «доля неуспешных» видит и такие вызовы.
    from app.services import llm_ledger

    while True:
        attempts += 1
        reply = ""
        attempt_rows: list[int] = []
        try:
            # call ВНУТРИ try: LlmContractError может прийти из глубины
            # вызова (volume-добор внутри chat) — она тоже repair'ится,
            # а не пролетает мимо петли (баг панели 2026-08-21).
            with llm_ledger.capture_attempt() as attempt_rows:
                reply = await call(f"{FEEDBACK_HEADER}\n{feedback}" if feedback else None)
            parsed = contract.parse(reply)
            problems = validate(parsed.payload) if validate else []
            if problems:
                raise LlmContractError(
                    "ответ прошёл схему, но не прошёл проверку:\n- "
                    + "\n- ".join(str(p) for p in problems[:12]),
                    kind="validate",
                    contract=contract.name,
                    detail={"problems": problems[:20]},
                )
        except LlmContractError as e:
            await llm_ledger.mark_contract_rejected(attempt_rows)
            if e.kind == "parse":
                parse_fails += 1
                exhausted = parse_fails > parse_limit
            else:
                validate_fails += 1
                exhausted = validate_fails > validate_limit
            _record(contract.name, reply, label=label, error=str(e), verdict=e.kind)
            rp = _write_reject(
                reject_dir,
                label=label or contract.name,
                attempt=attempts,
                reply=reply,
                error=e,
            )
            if rp is not None:
                rejected.append(rp)
            logger.warning(
                "contracts/policy {} attempt {} {}-fail ({}/{}): {}",
                label or contract.name,
                attempts,
                e.kind,
                parse_fails if e.kind == "parse" else validate_fails,
                parse_limit if e.kind == "parse" else validate_limit,
                str(e)[:300],
            )
            if exhausted:
                _write_metrics(
                    reject_dir,
                    contract=contract.name,
                    label=label or contract.name,
                    attempts=attempts,
                    parse_fails=parse_fails,
                    validate_fails=validate_fails,
                    ok=False,
                )
                raise LlmContractError(
                    f"{label or contract.name}: repair-лимит исчерпан "
                    f"(attempts={attempts}, parse_fails={parse_fails}, "
                    f"validate_fails={validate_fails}); последняя ошибка: {e}",
                    kind=e.kind,
                    contract=contract.name,
                    detail={
                        **e.detail,
                        "attempts": attempts,
                        "parse_fails": parse_fails,
                        "validate_fails": validate_fails,
                        "rejected_paths": [str(p) for p in rejected],
                    },
                ) from e
            feedback = e.feedback
            continue

        _record(contract.name, reply, label=label, verdict="ok")
        _write_metrics(
            reject_dir,
            contract=contract.name,
            label=label or contract.name,
            attempts=attempts,
            parse_fails=parse_fails,
            validate_fails=validate_fails,
            ok=True,
        )
        result = RepairResult(
            payload=parsed.payload,
            reply_text=reply,
            meta=parsed.meta,
            attempts=attempts,
            parse_fails=parse_fails,
            validate_fails=validate_fails,
            rejected_paths=rejected,
        )
        if result.repairs:
            logger.info(
                "contracts/policy {} ok после {} repair (parse={}, validate={})",
                label or contract.name,
                result.repairs,
                parse_fails,
                validate_fails,
            )
        return result
