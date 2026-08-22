"""Контракты scene_design-комплекса (A5-A11) и scene_grammar (A13).

Тонкие конверты ФОРМЫ: обязательный list-key агента (``agents.LIST_KEY``),
запрет ``error``-ответа, для сборщика — {characters, scenes, ops, report}
(``parse_assembler_payload``; тот же формат у A13 scene_grammar — спека).

Сознательно extra="allow" и без доменной валидации: нормализаторы и
V-правила (``normalize_skeleton_draft``, ``repair_chrono_dyn_year_jumps``,
``validate_chrono_dyn_*`` и т.д.) остаются в ``scene_design/agents.py`` —
живой код не переписываем (принцип этапа). На миграции C.3 политика зовёт
контракт (форма) + существующий ``parse_agent_slice`` (семантика), и
``SceneDesignAgentError`` оборачивается в ``LlmContractError``.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, model_validator

from app.contracts.base import LlmContract


class _SliceBase(BaseModel):
    model_config = ConfigDict(extra="allow")

    error: str | None = None

    @model_validator(mode="after")
    def _no_agent_error(self) -> _SliceBase:
        if (self.error or "").strip():
            raise ValueError(f"агент вернул error: {self.error}")
        return self


def _require_list(name: str, value: Any, *, allow_empty: bool = False) -> None:
    if not isinstance(value, list):
        raise ValueError(f"нет списка «{name}»")
    if not value and not allow_empty:
        raise ValueError(f"пустой «{name}» — срез не принят")


class SkeletonPayload(_SliceBase):
    """A5/A6 skeleton: scenes (V3: cells; legacy: «сцены»)."""

    scenes: list[Any] | None = None
    cells: list[Any] | None = None

    @model_validator(mode="after")
    def _scenes_or_cells(self) -> SkeletonPayload:
        if self.scenes is None and isinstance(self.model_extra, dict):
            alt = self.model_extra.get("сцены")
            if isinstance(alt, list):
                self.scenes = alt
        if not (self.scenes or self.cells):
            raise ValueError("skeleton: нет непустого scenes/cells")
        return self


class CharactersSlice(_SliceBase):
    characters: list[Any]

    @model_validator(mode="after")
    def _non_empty(self) -> CharactersSlice:
        _require_list("characters", self.characters)
        return self


class WorldSlice(_SliceBase):
    """world: пустой locations допустим (нет повторяемых мест)."""

    locations: list[Any]

    @model_validator(mode="after")
    def _is_list(self) -> WorldSlice:
        _require_list("locations", self.locations, allow_empty=True)
        return self


class CameraSlice(_SliceBase):
    shot_plan: list[Any]

    @model_validator(mode="after")
    def _non_empty(self) -> CameraSlice:
        _require_list("shot_plan", self.shot_plan)
        return self


class ActionSlice(_SliceBase):
    scenes: list[Any]

    @model_validator(mode="after")
    def _non_empty(self) -> ActionSlice:
        _require_list("scenes", self.scenes)
        return self


class AssemblePayload(_SliceBase):
    """A11 сборщик и A13 scene_grammar: {characters, scenes, ops, report}."""

    characters: list[Any]
    scenes: list[Any]
    ops: list[Any]
    report: Any | None = None

    @model_validator(mode="after")
    def _shape(self) -> AssemblePayload:
        _require_list("characters", self.characters, allow_empty=True)
        _require_list("scenes", self.scenes)
        _require_list("ops", self.ops)
        return self


SD_SKELETON = LlmContract(name="vp_sd_skeleton", model=SkeletonPayload)
SD_CHARACTERS = LlmContract(name="vp_sd_characters", model=CharactersSlice)
SD_WORLD = LlmContract(name="vp_sd_world", model=WorldSlice)
SD_CAMERA = LlmContract(name="vp_sd_camera", model=CameraSlice)
SD_ACTION = LlmContract(name="vp_sd_action", model=ActionSlice)
SD_ASSEMBLE = LlmContract(name="vp_sd_assemble", model=AssemblePayload)

# agent-имя из scene_design/agents.py → контракт (для миграции C.3)
SLICE_CONTRACTS: dict[str, LlmContract] = {
    "skeleton": SD_SKELETON,
    "characters": SD_CHARACTERS,
    "world": SD_WORLD,
    "camera": SD_CAMERA,
    "action": SD_ACTION,
    "assemble": SD_ASSEMBLE,
}
