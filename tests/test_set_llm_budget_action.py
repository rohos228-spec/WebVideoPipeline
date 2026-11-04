"""Чат поднимает бюджет текстов проекта: действие есть в реестре, пишет бюджет и снимает паузу."""

from __future__ import annotations

import pytest

from app.services.action_layer.builtin_wave5 import _set_llm_budget
from app.services.action_layer.step_outcome import stop_reason


class _Ctx:
    def __init__(self, reply: dict) -> None:
        self.reply = reply
        self.calls: list[tuple] = []

    async def call(self, method: str, path: str, **kw):
        self.calls.append((method, path, kw))
        return self.reply


@pytest.mark.asyncio
async def test_set_llm_budget_posts_and_reports_cleared_pause() -> None:
    ctx = _Ctx({"project_id": 26, "budget_usd": 1.0, "pause_reason_cleared": True})
    out = await _set_llm_budget(ctx, {"project_id": 26, "amount_usd": 1})
    assert ctx.calls == [("POST", "/projects/26/llm-budget", {"json": {"budget_usd": 1.0}})]
    assert out["pause_cleared"] is True
    assert "теперь $1." in out["summary_ru"] and "Пауза" in out["summary_ru"]


def test_action_is_registered_and_needs_confirmation_tier() -> None:
    from app.services.action_layer.spec import _builtin_spec_list

    spec = next(s for s in _builtin_spec_list() if s.id == "project.set_llm_budget")
    assert spec.risk == "local_safe"
    assert spec.params_schema["required"] == ["project_id", "amount_usd"]


def test_budget_pause_reason_tells_how_to_continue() -> None:
    meta = {
        "auto_paused_reason": "бюджет исчерпан: $0.15 из $0.10",
        "pause_reason": {"code": "budget_exhausted", "spent_usd": 0.15, "budget_usd": 0.1},
    }
    text = stop_reason(meta)
    assert "бюджет исчерпан" in text
    assert "подними бюджет текстов проекта" in text


def test_contract_is_medium_not_deletion() -> None:
    """Без строки в контрактах новое действие считалось удалением и чат его блокировал."""
    from app.orchestrator.actions.executor import metadata_of

    meta = metadata_of("project.set_llm_budget")
    assert (meta.risk, meta.destructive, meta.paid) == ("medium", False, False)


@pytest.mark.asyncio
async def test_run_card_warns_when_text_budget_is_spent(monkeypatch) -> None:
    from app.services import llm_ledger
    from app.services.action_layer import builtin_launch as bl

    async def budget(pid, fresh=False):
        return 0.1

    async def spent(pid, fresh=False):
        return 0.15

    monkeypatch.setattr(llm_ledger, "budget_usd", budget)
    monkeypatch.setattr(llm_ledger, "spent_usd", spent)
    note = await bl.text_budget_warning(26)
    assert note and "$0.15 из $0.10" in note and "до 2 доллара" in note

    async def spent_low(pid, fresh=False):
        return 0.05

    monkeypatch.setattr(llm_ledger, "spent_usd", spent_low)
    assert await bl.text_budget_warning(26) is None


def test_run_cap_pause_has_no_text_budget_hint() -> None:
    meta = {
        "auto_paused_reason": "потолок запуска исчерпан: $0.31 из $0.30",
        "pause_reason": {"code": "budget_exhausted", "kind": "run_cap"},
    }
    assert "подними бюджет" not in stop_reason(meta)


def test_run_cap_exception_text_differs_from_project_budget() -> None:
    from app.services.llm_ledger import BudgetExhausted

    assert str(BudgetExhausted(project_id=1, spent_usd=1, budget_usd=1)).startswith("бюджет исчерпан")
    assert str(BudgetExhausted(project_id=1, spent_usd=1, budget_usd=1, kind="run_cap")).startswith(
        "потолок запуска исчерпан"
    )
