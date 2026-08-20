"""Единая ошибка контрактов LLM (этап 5, llm-contracts).

`LlmContractError` поднимается на любом мигрированном пути при невалидном
ответе модели. Текст ошибки — человекочитаемый и идёт фидбеком в
repair-промпт следующей попытки (политика D, по образцу
``ai_result_io.text_job``). Заменяет 4 несовместимых поведения as-is:
raise / warning+continue / тихий None / fallback без LLM.
"""

from __future__ import annotations

from typing import Any


class LlmContractError(ValueError):
    """Невалидный LLM-ответ на контрактном пути.

    ``kind``: parse (не JSON / не извлекается) | validate (JSON есть,
    схема нарушена) — раздельные лимиты в repair-политике.
    ``detail`` — контекст для логов/диска (contract, ошибки Pydantic).
    """

    def __init__(
        self,
        message: str,
        *,
        kind: str = "validate",
        contract: str = "",
        detail: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.contract = contract
        self.detail = detail or {}

    @property
    def feedback(self) -> str:
        """Текст для секции «ошибки прошлой попытки» repair-промпта."""
        return str(self)
