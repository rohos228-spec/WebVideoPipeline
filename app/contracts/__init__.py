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
from app.contracts.check_report import CHECK_REPORT, CheckReport
from app.contracts.policy import RepairResult, run_with_contract
from app.contracts.prompt_ops import (
    ANIM_PR,
    IMG_PR,
    VOICEOVER,
    AnimPrEnvelope,
    ImgPrEnvelope,
    VoiceoverEnvelope,
)
from app.contracts.scene_design import (
    SD_ACTION,
    SD_ASSEMBLE,
    SD_CAMERA,
    SD_CHARACTERS,
    SD_SKELETON,
    SD_WORLD,
    SLICE_CONTRACTS,
    AssemblePayload,
    SkeletonPayload,
)
from app.contracts.split import FRAME_SPLIT, FrameSpecItem, FrameSplitEnvelope

_REGISTRY: dict[str, LlmContract] = {
    c.name: c
    for c in (
        APPLY_OPS,
        FRAME_SPLIT,
        IMG_PR,
        ANIM_PR,
        VOICEOVER,
        SD_SKELETON,
        SD_CHARACTERS,
        SD_WORLD,
        SD_CAMERA,
        SD_ACTION,
        SD_ASSEMBLE,
        CHECK_REPORT,
    )
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
    "ANIM_PR",
    "APPLY_OPS",
    "CHECK_REPORT",
    "FRAME_SPLIT",
    "IMG_PR",
    "SD_ACTION",
    "SD_ASSEMBLE",
    "SD_CAMERA",
    "SD_CHARACTERS",
    "SD_SKELETON",
    "SD_WORLD",
    "SLICE_CONTRACTS",
    "VOICEOVER",
    "AnimPrEnvelope",
    "ApplyOp",
    "ApplyOpsEnvelope",
    "AssemblePayload",
    "CheckReport",
    "FrameSpecItem",
    "FrameSplitEnvelope",
    "ImgPrEnvelope",
    "LlmContract",
    "LlmContractError",
    "ParsedReply",
    "RepairResult",
    "SkeletonPayload",
    "VoiceoverEnvelope",
    "extract_json_payload",
    "get_contract",
    "register",
    "run_with_contract",
]
