"""Единственная точка вызова модели для агента стиля.

Все специалисты ходят через ``ask()``: проверка бюджета → ``gpt_client``
→ журнал ``llm_calls.jsonl``. Тесты подменяют ``ask`` (monkeypatch) —
платных вызовов в тестах нет.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loguru import logger

from app.services.style_agent.budget import BudgetExceeded, LlmBudget, is_fatal_provider_error
from app.services.style_analyzer import parse_json_object, strip_markdown_fences

DEFAULT_TIMEOUT = 420.0


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _append_log(log_path: Path | None, record: dict[str, Any]) -> None:
    if log_path is None:
        return
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as exc:
        logger.warning("style_agent: не записать llm_calls.jsonl: {}", exc)


async def ask(
    text: str,
    *,
    files: list[Path] | None = None,
    system: str | None = None,
    budget: LlmBudget,
    label: str = "",
    project_id: int | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    log_path: Path | None = None,
) -> str:
    """Один запрос к текущей модели (``llm_override`` уже привязан снаружи)."""
    n = budget.reserve(label)
    from app.services.gpt_client import get_gpt_client

    client = get_gpt_client()
    started = time.monotonic()
    err: str | None = None
    raw = ""
    try:
        raw = await client.ask_with_files(
            text,
            list(files or []),
            system=system,
            timeout=timeout,
            max_retries=1,
            project_id=project_id,
        )
        return raw
    except Exception as exc:  # noqa: BLE001
        err = str(exc)
        if is_fatal_provider_error(err):
            budget.fail(err)
            logger.warning("style_agent: провайдер отказал, дальнейшие вызовы остановлены: {}", err[:200])
            raise BudgetExceeded(f"провайдер отказал (баланс/доступ): {err[:200]}") from exc
        raise
    finally:
        _append_log(
            log_path,
            {
                "n": n,
                "label": label,
                "at": _utc_now(),
                "files": [p.name for p in (files or [])],
                "prompt_chars": len(text or ""),
                "reply_chars": len(raw or ""),
                "elapsed_s": round(time.monotonic() - started, 2),
                "error": err,
            },
        )


async def ask_json(
    text: str,
    *,
    files: list[Path] | None = None,
    system: str | None = None,
    budget: LlmBudget,
    label: str = "",
    project_id: int | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    log_path: Path | None = None,
) -> tuple[dict[str, Any] | None, str]:
    """``ask`` + разбор JSON-объекта (``style_analyzer.parse_json_object``)."""
    raw = await ask(
        text,
        files=files,
        system=system,
        budget=budget,
        label=label,
        project_id=project_id,
        timeout=timeout,
        log_path=log_path,
    )
    data = parse_json_object(strip_markdown_fences(raw or ""))
    if data is None and raw:
        data = repair_json_object(raw)
        if data is not None:
            logger.info("style_agent: JSON {} восстановлен (repair), ключей {}", label, len(data))
    return data, raw or ""


def _largest_brace_block(text: str) -> str | None:
    """Самый длинный сбалансированный {...} блок (строки/экранирование учитываются)."""
    best: tuple[int, int] | None = None
    stack = 0
    start = -1
    in_str = False
    esc = False
    for i, ch in enumerate(text):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if stack == 0:
                start = i
            stack += 1
        elif ch == "}" and stack > 0:
            stack -= 1
            if stack == 0 and start >= 0 and (best is None or i + 1 - start > best[1] - best[0]):
                best = (start, i + 1)
    return text[best[0] : best[1]] if best else None


def _close_truncated(text: str) -> str | None:
    """Обрезанный JSON → отрезать до последнего завершённого элемента и закрыть скобки."""
    i0 = text.find("{")
    if i0 < 0:
        return None
    t = text[i0:]
    stack: list[str] = []
    in_str = False
    esc = False
    last_ok = -1
    for i, ch in enumerate(t):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in "{[":
            stack.append("}" if ch == "{" else "]")
        elif ch in "}]":
            if stack:
                stack.pop()
            last_ok = i
        elif ch == "," and stack:
            last_ok = i - 1
    if last_ok < 0:
        return None
    head = t[: last_ok + 1].rstrip().rstrip(",")
    # пересчитать незакрытые скобки для обрезанной головы
    stack = []
    in_str = False
    esc = False
    for ch in head:
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in "{[":
            stack.append("}" if ch == "{" else "]")
        elif ch in "}]" and stack:
            stack.pop()
    return head + "".join(reversed(stack))


def repair_json_object(raw: str) -> dict[str, Any] | None:
    """Устойчивый разбор ответа: самый большой JSON-блок → починка обрезанного хвоста."""
    text = strip_markdown_fences(raw or "")
    for cand in (_largest_brace_block(text), _close_truncated(text)):
        if not cand:
            continue
        try:
            obj = json.loads(cand)
        except ValueError:
            obj = parse_json_object(cand)
        if isinstance(obj, dict) and obj:
            return obj
    return None
