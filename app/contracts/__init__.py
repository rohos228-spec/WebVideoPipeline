"""Реестр контрактов LLM-выходов (этап 5, llm-contracts).

Единственный источник схем для мигрированных агентов. Использование:

    from app.contracts import APPLY_OPS, LlmContractError
    parsed = APPLY_OPS.parse(reply_text)          # LlmContractError при браке
    schema = APPLY_OPS.response_schema()          # → chat(response_schema=…)

Состав (спека Req 2): APPLY_OPS (A12/A14); FrameSpec/split, ImgPrOps,
scene_design-комплекс, CheckReport, AnimPrOps, VoiceoverPayload —
добавляются задачами B.4-B.6.
"""

from __future__ import annotations

from app.contracts.apply_ops import APPLY_OPS, ApplyOp, ApplyOpsEnvelope
from app.contracts.base import LlmContract, ParsedReply
from app.contracts.errors import LlmContractError
from app.contracts.extract import extract_json_payload

_REGISTRY: dict[str, LlmContract] = {
    APPLY_OPS.name: APPLY_OPS,
}


def get_contract(name: str) -> LlmContract:
    try:
        return _REGISTRY[name]
    except KeyError:
        raise KeyError(
            f"неизвестный контракт {name!r}; есть: {sorted(_REGISTRY)}"
        ) from None


def register(contract: LlmContract) -> LlmContract:
    """Для новых контрактов (шаблон миграции E.1)."""
    _REGISTRY[contract.name] = contract
    return contract


__all__ = [
    "APPLY_OPS",
    "ApplyOp",
    "ApplyOpsEnvelope",
    "LlmContract",
    "LlmContractError",
    "ParsedReply",
    "extract_json_payload",
    "get_contract",
    "register",
]
