"""Узкие apply-ops контракты: img_pr (A15), anim_pr (A16), voiceover.

Наследуют ApplyOpsEnvelope (та же форма, те же алиасы) и добавляют
allowlist полей своей ноды — зеркало ``node_write_contract`` allowlist'ов:
img_pr → image_prompt/image_prompt_shot2/characters, anim_pr →
animation_prompt/animation_prompt_shot2, voiceover → voiceover_text
(frame) / script_text (project). Покрытие uuid N/N — не здесь: это
``node_write_contract.coverage_report`` ПОСЛЕ склейки батчей (спека:
схемная валидность части ≠ успех целого).

Strict-схемы img_pr/anim_pr — ручные (канонические ключи, required-all,
nullable для опциональных): pydantic-генерённая схема в OpenAI strict
невалидна. Валидация ответа — всё равно моделью с алиасами.
"""

from __future__ import annotations

from pydantic import model_validator

from app.contracts.apply_ops import ApplyOpsEnvelope
from app.contracts.base import LlmContract

_IMG_PR_FIELDS = frozenset({"image_prompt", "image_prompt_shot2", "characters"})
_ANIM_PR_FIELDS = frozenset({"animation_prompt", "animation_prompt_shot2"})


def _check_ops_allowlist(
    env: ApplyOpsEnvelope,
    *,
    allowed: frozenset[str],
    required_one_of: frozenset[str],
    node: str,
) -> None:
    """Все ops — target=frame; canon-поля ⊆ allowed; хотя бы одно из required."""
    for op in env.ops:
        if op.target != "frame":
            raise ValueError(f"{node}: допустим только target=frame, получен {op.target}")
        fields = op.fields or {}
        # Поля уже канонизированы валидатором ApplyOp (fields → canon).
        extra = sorted(set(fields) - allowed)
        if extra:
            raise ValueError(
                f"{node}: кадр {op.frame_uuid}: поля {extra} этой ноде "
                f"писать нельзя; разрешены {sorted(allowed)}"
            )
        present = {k for k, v in fields.items() if k in required_one_of and str(v or "").strip()}
        if not present:
            raise ValueError(f"{node}: кадр {op.frame_uuid}: нет непустого {sorted(required_one_of)}")


class ImgPrEnvelope(ApplyOpsEnvelope):
    """img_pr: промпты картинок (+ characters) по кадрам."""

    @model_validator(mode="after")
    def _img_pr_allowlist(self) -> ImgPrEnvelope:
        # null-значения от strict-схемы (required-all: неиспользуемое поле
        # модель обязана прислать как null) — убрать до allowlist-проверки.
        for op in self.ops:
            if op.fields:
                op.fields = {k: v for k, v in op.fields.items() if v is not None}
        _check_ops_allowlist(
            self,
            allowed=_IMG_PR_FIELDS,
            required_one_of=frozenset({"image_prompt", "image_prompt_shot2"}),
            node="img_pr",
        )
        return self


class AnimPrEnvelope(ApplyOpsEnvelope):
    """anim_pr: промпты анимации по кадрам."""

    @model_validator(mode="after")
    def _anim_pr_allowlist(self) -> AnimPrEnvelope:
        for op in self.ops:
            if op.fields:
                op.fields = {k: v for k, v in op.fields.items() if v is not None}
        _check_ops_allowlist(
            self,
            allowed=_ANIM_PR_FIELDS,
            required_one_of=_ANIM_PR_FIELDS,
            node="anim_pr",
        )
        return self


class VoiceoverEnvelope(ApplyOpsEnvelope):
    """voiceover: закадр по кадрам (frame) либо весь сценарий (project)."""

    @model_validator(mode="after")
    def _voiceover_allowlist(self) -> VoiceoverEnvelope:
        for op in self.ops:
            fields = op.fields or {}
            if op.target == "frame":
                extra = sorted(set(fields) - {"voiceover_text"})
                if extra or not str(fields.get("voiceover_text") or "").strip():
                    raise ValueError(
                        f"voiceover: кадр {op.frame_uuid}: нужно ровно поле voiceover_text (лишние: {extra})"
                    )
            elif op.target == "project":
                if not str(fields.get("script_text") or "").strip():
                    raise ValueError("voiceover: project-операция без script_text")
            else:
                raise ValueError(f"voiceover: target {op.target} не допускается")
        return self


_IMG_PR_STRICT_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "required": ["ops"],
    "properties": {
        "ops": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["frame_uuid", "fields"],
                "properties": {
                    "frame_uuid": {"type": "string"},
                    "fields": {
                        "type": "object",
                        "additionalProperties": False,
                        # strict: required-all, неиспользуемые поля — null
                        # (баг панели 2026-08-21: без shot2-полей в схеме
                        # enforced-релей физически не мог их вернуть).
                        "required": [
                            "image_prompt",
                            "image_prompt_shot2",
                            "characters",
                        ],
                        "properties": {
                            "image_prompt": {"type": ["string", "null"]},
                            "image_prompt_shot2": {"type": ["string", "null"]},
                            "characters": {"type": ["string", "null"]},
                        },
                    },
                },
            },
        }
    },
}

_ANIM_PR_STRICT_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "required": ["ops"],
    "properties": {
        "ops": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["frame_uuid", "fields"],
                "properties": {
                    "frame_uuid": {"type": "string"},
                    "fields": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["animation_prompt", "animation_prompt_shot2"],
                        "properties": {
                            "animation_prompt": {"type": ["string", "null"]},
                            "animation_prompt_shot2": {"type": ["string", "null"]},
                        },
                    },
                },
            },
        }
    },
}

IMG_PR = LlmContract(
    name="vp_img_pr",
    model=ImgPrEnvelope,
    strict=True,
    schema_override=_IMG_PR_STRICT_SCHEMA,
)
ANIM_PR = LlmContract(
    name="vp_anim_pr",
    model=AnimPrEnvelope,
    strict=True,
    schema_override=_ANIM_PR_STRICT_SCHEMA,
)
VOICEOVER = LlmContract(name="vp_voiceover", model=VoiceoverEnvelope)
