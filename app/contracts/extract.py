"""Канонический JSON-экстрактор контрактного пути.

Единственный экстрактор для мигрированных агентов (заменяет 8 самописных
из карты §4.3 по мере миграции). Порядок: целый текст → ```json-fence →
первый сбалансированный объект (строко-осознанный автомат: `{` внутри
строк не считается — баг экстрактора №5 из карты).

Salvage обрезанного JSON здесь НЕТ сознательно: по спеке salvage допустим
только с маркером и добором до полного покрытия — это уровень политики
(db_apply.salvage_ops_from_partial_json), не экстрактора.
"""

from __future__ import annotations

import json
import re
from typing import Any

from app.contracts.errors import LlmContractError

_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def _balanced_object(text: str, start: int) -> str | None:
    """Вернуть сбалансированный {...} от ``start``, учитывая строки."""
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
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
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def extract_json_payload(text: str, *, contract: str = "") -> dict[str, Any]:
    """Достать JSON-объект из ответа модели.

    Raises:
        LlmContractError(kind="parse") — с текстом, пригодным для
        repair-фидбека (что не так и что ожидалось).
    """
    raw = (text or "").strip()
    if not raw:
        raise LlmContractError(
            "пустой ответ — ожидался JSON-объект",
            kind="parse",
            contract=contract,
        )
    # 1. Целый текст — уже JSON.
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    # 2. ```json-fence.
    m = _FENCE_RE.search(raw)
    if m:
        try:
            data = json.loads(m.group(1))
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass
    # 3. Первый сбалансированный объект.
    idx = raw.find("{")
    while idx != -1:
        candidate = _balanced_object(raw, idx)
        if candidate is not None:
            try:
                data = json.loads(candidate)
                if isinstance(data, dict):
                    return data
            except json.JSONDecodeError:
                pass
        idx = raw.find("{", idx + 1)
    raise LlmContractError(
        "в ответе нет валидного JSON-объекта (проза/обрыв/битые кавычки). "
        "Верни ТОЛЬКО JSON без пояснений и markdown-fence",
        kind="parse",
        contract=contract,
        detail={"chars": len(raw), "head": raw[:200]},
    )
