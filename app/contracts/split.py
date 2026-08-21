"""Контракт split (A3): полная замена кадров — replace_frames.

Форма повторяет вход ``db_v2.replace_all_frames``: список спеков
``{"voiceover_text"|"закадр", "duration_seconds"|"длительность"?,
"meaning"|"смысл"?, "uuid"?}``, завёрнутый в apply-ops
``{"ops":[{"target":"replace_frames","frames":[…]}]}`` (как требует
``_SPLIT_DB_HINT``). Правило ≥2 кадров — из ``replace_all_frames``.

Локальная разбивка ``split_voiceover_locally`` контрактом не является —
это деградация ``degraded_no_llm`` (спека «Запрет тихого частичного
успеха»), решает политика шага, не схема.
"""

from __future__ import annotations

from typing import Literal

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from app.contracts.base import LlmContract


class FrameSpecItem(BaseModel):
    """Один кадр replace_frames (см. db_v2.replace_all_frames)."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    voiceover_text: str = Field(
        validation_alias=AliasChoices("voiceover_text", "закадр", "реплика")
    )
    duration_seconds: float | int | str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "duration_seconds", "длительность", "время", "секунды"
        ),
    )
    meaning: str | None = Field(
        default=None, validation_alias=AliasChoices("meaning", "смысл")
    )
    uuid: str | None = None

    @field_validator("voiceover_text")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not (v or "").strip():
            raise ValueError("пустой закадр кадра")
        return v


class ReplaceFramesOp(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    target: Literal["replace_frames"]
    frames: list[FrameSpecItem] = Field(
        validation_alias=AliasChoices("frames", "кадры")
    )

    @model_validator(mode="after")
    def _min_two(self) -> "ReplaceFramesOp":
        if len(self.frames) < 2:
            raise ValueError(
                f"replace_frames: нужно ≥2 кадра, получили {len(self.frames)}"
            )
        return self


class FrameSplitEnvelope(BaseModel):
    """Ответ split: ровно одна операция replace_frames."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    ops: list[ReplaceFramesOp] = Field(
        validation_alias=AliasChoices("ops", "actions")
    )

    @model_validator(mode="after")
    def _exactly_one(self) -> "FrameSplitEnvelope":
        if len(self.ops) != 1:
            raise ValueError(
                "split: ровно одна операция replace_frames "
                f"(получили {len(self.ops)})"
            )
        return self

    @property
    def frames(self) -> list[FrameSpecItem]:
        return self.ops[0].frames


FRAME_SPLIT = LlmContract(name="vp_frame_split", model=FrameSplitEnvelope)
