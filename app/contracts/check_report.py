"""Контракт отчёта check-ноды — JSON vp.check.v1 (A17, задача C.5).

Форма — RESPONSE_FOOTER из ``check_analysis.py``: schema/verdict/summary/
checks/forward/fix. Валидируется JSON-ветка ответа; TXT-отчёт остаётся
параллельным допустимым форматом (``parse_check_report_txt``) — решает
политика на миграции C.5.

Смысл контракта — спека «Битый ответ проверки ≠ вердикт fail»: мусор/
эхо apply-ops/JSON без verdict должны давать LlmContractError → repair,
а не молчаливый fail (as-is ``check_analysis.py:1334/:1346/:1351``),
который неотличим от реального брака и запускает платный regen-цикл.

extra="allow": реальные отчёты несут доп. ключи (scores, decision, …) —
их читает существующий парсер; контракт типизирует ядро.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.contracts.base import LlmContract

SCHEMA_ID = "vp.check.v1"


class CheckItem(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str = ""
    ok: bool | None = None
    note: str = ""


class CheckForward(BaseModel):
    model_config = ConfigDict(extra="allow")

    mode: Literal["inherit", "explicit"] = "inherit"
    paths: list[str] = Field(default_factory=list)


class CheckFix(BaseModel):
    model_config = ConfigDict(extra="allow")

    target: Literal["source", "xlsx", "prompt", "none"] = "none"
    instructions: str = ""
    rewrite_file: str | None = None


class CheckReport(BaseModel):
    """JSON-отчёт проверки vp.check.v1."""

    model_config = ConfigDict(extra="allow")

    schema_id: str | None = Field(default=None, alias="schema")
    verdict: Literal["pass", "fail"] | None = None
    summary: str = ""
    checks: list[CheckItem] = Field(default_factory=list)
    forward: CheckForward = Field(default_factory=CheckForward)
    fix: CheckFix = Field(default_factory=CheckFix)

    @model_validator(mode="after")
    def _verdict_required(self) -> CheckReport:
        if self.schema_id and self.schema_id != SCHEMA_ID:
            raise ValueError(f"неверная schema: {self.schema_id!r}, ожидается {SCHEMA_ID}")
        if self.verdict is None:
            # Синонимы из старых промптов (decision) — как в parse_check_analysis.
            decision = ""
            if isinstance(self.model_extra, dict):
                decision = str(self.model_extra.get("decision") or "").strip().lower()
            if decision in ("approved", "approve", "ok"):
                self.verdict = "pass"
            elif decision in ("rejected", "regen", "reject", "fail"):
                self.verdict = "fail"
            else:
                raise ValueError(
                    'в отчёте нет поля verdict ("pass"|"fail") — это не vp.check.v1-отчёт проверки'
                )
        return self


CHECK_REPORT = LlmContract(name="vp_check_report", model=CheckReport)
