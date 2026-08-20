"""Базовый контракт LLM-выхода (этап 5, llm-contracts).

Контракт = Pydantic-модель + имя + правила извлечения. Один контракт —
все call-site одного типа ответа (спека: «Один контракт — все call-site»).

Использование:
    parsed = CONTRACT.parse(reply_text)      # → ParsedReply | LlmContractError
    schema = CONTRACT.response_schema()      # → gpt_api.ResponseSchema | None
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ValidationError

from app.contracts.errors import LlmContractError
from app.contracts.extract import extract_json_payload

TModel = TypeVar("TModel", bound=BaseModel)

# Служебные ключи payload'а, не входящие в схему (снимаются ДО валидации;
# extra=forbid иначе убил бы сам маркер — находка панели 2026-08-21).
_META_KEYS = ("_salvaged_partial",)


@dataclass
class ParsedReply(Generic[TModel]):
    payload: TModel
    # Снятые до валидации маркеры (_salvaged_partial и т.п.) — для NodeRun.meta.
    meta: dict[str, Any] = field(default_factory=dict)


def _format_validation_error(e: ValidationError, *, contract: str) -> str:
    """Ошибка Pydantic → компактный фидбек для repair-промпта."""
    lines = []
    for err in e.errors()[:12]:
        loc = ".".join(str(p) for p in err.get("loc") or ())
        lines.append(f"- {loc or '<root>'}: {err.get('msg')}")
    extra = len(e.errors()) - 12
    if extra > 0:
        lines.append(f"- … и ещё {extra} ошибок того же рода")
    return f"схема {contract} нарушена:\n" + "\n".join(lines)


@dataclass(frozen=True)
class LlmContract(Generic[TModel]):
    """Именованный контракт: извлечение JSON + валидация моделью."""

    name: str
    model: type[TModel]
    # strict-совместимость json_schema (см. gpt_api.ResponseSchema.strict).
    strict: bool = True

    def parse(self, text: str) -> ParsedReply[TModel]:
        data = extract_json_payload(text, contract=self.name)
        meta = {k: data.pop(k) for k in _META_KEYS if k in data}
        try:
            payload = self.model.model_validate(data)
        except ValidationError as e:
            raise LlmContractError(
                _format_validation_error(e, contract=self.name),
                kind="validate",
                contract=self.name,
                detail={"errors": e.errors()[:20]},
            ) from e
        return ParsedReply(payload=payload, meta=meta)

    def json_schema(self) -> dict[str, Any]:
        return self.model.model_json_schema()

    def response_schema(self) -> Any:
        """gpt_api.ResponseSchema для прокидки в chat(response_schema=…)."""
        from app.services.gpt_api import ResponseSchema

        return ResponseSchema(
            name=self.name, schema=self.json_schema(), strict=self.strict
        )
