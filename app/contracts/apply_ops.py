"""Контракт apply-ops (A12 ядро; тот же путь — A14 character_registry).

Схема повторяет фактический вход ``db_apply.apply_ops``:
``{"ops":[{"target","frame_uuid","fields"|"frames"}], "characters", "scenes"}``.

Алиасы полей: словарь ``db_apply.FIELD_ALIASES`` остаётся ЕДИНСТВЕННЫМ
источником (решение панель-ревью 2026-08-21) — контракт читает его лениво,
немигрированные потребители продолжают ходить в ``normalize_fields``.
Канон-нормализация ключа — как ``db_apply._canon_key`` (lower/strip/
пробелы→``_``), иначе «Промт Картинки» начал бы падать там, где сегодня
чинится. Два синонима одного поля — last-write-wins с логом (поведение
as-is, не «неизвестное поле»).

Семантические ремонты uuid (Хэмминг, номер→uuid) здесь НЕ дублируются —
они остаются в ``db_apply.apply_ops`` (им нужен список кадров из БД);
контракт валидирует форму, не существование uuid.
"""

from __future__ import annotations

from typing import Any, Literal

from loguru import logger
from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)

from app.contracts.base import LlmContract


def canonicalize_fields(raw: dict[str, Any], *, scope: str, project_scope: bool = False) -> dict[str, Any]:
    """Ключи fields → канонические имена через FIELD_ALIASES.

    Raises:
        ValueError: неизвестные ключи (Pydantic обернёт в ValidationError,
        контракт — в LlmContractError с фидбеком).
    """
    from app.services.db_apply import FIELD_ALIASES, PROJECT_FIELD_ALIASES

    aliases = PROJECT_FIELD_ALIASES if project_scope else FIELD_ALIASES
    out: dict[str, Any] = {}
    unknown: list[str] = []
    dup: list[str] = []
    for k, v in (raw or {}).items():
        canon = aliases.get(str(k).strip().lower().replace(" ", "_"))
        if canon is None:
            unknown.append(str(k))
            continue
        if canon in out:
            dup.append(str(k))
        out[canon] = v  # last-write-wins — как текущий normalize_fields
    if dup:
        logger.warning(
            "contracts/apply_ops: {} — дубль-синонимы одного поля {} (last-write-wins)",
            scope,
            dup,
        )
    if unknown:
        allowed = sorted(set(aliases.values()))
        raise ValueError(
            f"{scope}: неизвестные поля {unknown}. Канонические имена: "
            f"{allowed} (русские синонимы тоже принимаются)"
        )
    return out


class ApplyOp(BaseModel):
    """Одна операция: target=frame | project | replace_frames."""

    model_config = ConfigDict(extra="forbid")

    target: Literal["frame", "project", "replace_frames"] = "frame"
    frame_uuid: str | None = None
    fields: dict[str, Any] | None = None
    frames: list[Any] | None = Field(default=None, validation_alias=AliasChoices("frames", "кадры"))

    @model_validator(mode="after")
    def _shape_by_target(self) -> ApplyOp:
        if self.target == "frame":
            if not (self.frame_uuid or "").strip():
                raise ValueError("для target=frame нужен frame_uuid")
            if not self.fields:
                raise ValueError(f"кадр {self.frame_uuid}: пустые fields — нечего применять")
            self.fields = canonicalize_fields(self.fields, scope=f"кадр {self.frame_uuid}")
        elif self.target == "project":
            if not self.fields:
                raise ValueError("проект: пустые fields")
            self.fields = canonicalize_fields(self.fields, scope="проект", project_scope=True)
        else:  # replace_frames
            if not isinstance(self.frames, list) or not self.frames:
                raise ValueError("replace_frames: нужен непустой список frames")
        return self


# Ключи, которые конверт умеет применить. Всё прочее на верхнем уровне —
# болтовня модели (`report`, `notes`, `summary`), а не данные.
_ENVELOPE_KEYS = frozenset({"ops", "actions", "characters", "scenes"})
# Признаки того, что модель прислала ОДНУ операцию без конверта.
_BARE_OP_KEYS = frozenset({"target", "frame_uuid", "fields", "frames", "кадры"})


def _looks_like_payload(value: Any) -> bool:
    """Похоже ли значение на операции/кадры, а не на текст-комментарий."""
    if isinstance(value, dict):
        return bool(_BARE_OP_KEYS & value.keys())
    if isinstance(value, list):
        return any(isinstance(v, dict) and (_BARE_OP_KEYS & v.keys()) for v in value)
    return False


class ApplyOpsEnvelope(BaseModel):
    """Конверт ответа apply-ops: ops + опц. characters/scenes."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    ops: list[ApplyOp] = Field(default_factory=list, validation_alias=AliasChoices("ops", "actions"))
    characters: list[dict[str, Any]] | None = None
    scenes: list[dict[str, Any]] | None = None

    @model_validator(mode="before")
    @classmethod
    def _tolerate_wrapper_noise(cls, data: Any) -> Any:
        """Починить форму конверта, не трогая содержимое операций.

        Провайдер без structured outputs (MiniMax) регулярно добавляет к
        валидному пакету поле-комментарий или отдаёт одну операцию без
        конверта. Ронять из-за этого 16 КБ разобранной работы и жечь
        repair-ретраи — дороже, чем выкинуть болтовню с предупреждением.

        Строгость там, где она защищает данные, остаётся: внутри `ApplyOp`
        лишний ключ по-прежнему ошибка — там он означает поле, которое
        конвейер молча не применит.
        """
        if not isinstance(data, dict):
            return data
        if not (_ENVELOPE_KEYS & data.keys()) and (_BARE_OP_KEYS & data.keys()):
            logger.warning("contracts/apply_ops: операция без конверта — оборачиваю в ops[]")
            return {"ops": [data]}
        extra = {k: v for k, v in data.items() if k not in _ENVELOPE_KEYS}
        if not extra:
            return data
        misplaced = sorted(k for k, v in extra.items() if _looks_like_payload(v))
        if misplaced:
            # Под чужим именем лежит структура операций — выбросить её значит
            # потерять работу молча. Это по-прежнему ошибка контракта.
            raise ValueError(
                f"поля {misplaced} похожи на операции, но лежат вне ops[]. "
                f"Разрешённые ключи конверта: {sorted(_ENVELOPE_KEYS)}"
            )
        logger.warning(
            "contracts/apply_ops: комментарии в конверте {} — выброшены (не данные)",
            sorted(extra),
        )
        return {k: v for k, v in data.items() if k in _ENVELOPE_KEYS}

    @model_validator(mode="after")
    def _not_empty_and_replace_rules(self) -> ApplyOpsEnvelope:
        if not self.ops and not self.characters and not self.scenes:
            raise ValueError("пустой ops — нечего применять")
        replace_n = sum(1 for op in self.ops if op.target == "replace_frames")
        if replace_n > 1:
            raise ValueError("replace_frames: только одна операция за раз")
        if replace_n and len(self.ops) > 1:
            raise ValueError("replace_frames нельзя смешивать с другими ops в одном запросе")
        return self

    def frame_uuids(self) -> list[str]:
        return [str(op.frame_uuid) for op in self.ops if op.target == "frame" and op.frame_uuid]


# strict=False: fields — словарь с динамическими ключами (алиасы),
# в OpenAI strict-режиме невыразим (см. ResponseSchema.strict).
APPLY_OPS = LlmContract(name="vp_apply_ops", model=ApplyOpsEnvelope, strict=False)
