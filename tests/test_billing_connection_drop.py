"""Тесты биллинга при сбое соединения и частичных LLM-вызовах."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models import LlmCall, Project
from app.services import cost_recount, credit_ledger
from app.services.step_billing import _spent_since, step_billing
from tests import accounts_harness as ah


@pytest.mark.asyncio
async def test_failed_llm_calls_not_billed_on_connection_drop(tmp_path, monkeypatch) -> None:
    """Проверка: упавшие вызовы (result='error') не включаются в списание."""
    ah.configure(monkeypatch)
    _engine, factory = await ah.make_engine(tmp_path / "billing-drop.db")
    ah.bind_identity_session(monkeypatch, factory)

    async with factory() as session:
        proj = Project(slug="drop-test", topic="Connection Drop Test")
        session.add(proj)
        await session.commit()
        await session.refresh(proj)
        proj_id = proj.id

        # Создаём два вызова: один успешный ($0.05), один оборвавшийся при сбое ($0.03)
        ok_call = LlmCall(
            project_id=proj_id,
            node_key="img_pr",
            logical_call_id="call-ok",
            model="gpt-4o",
            result="ok",
            cost_usd=0.05,
        )
        failed_call = LlmCall(
            project_id=proj_id,
            node_key="img_pr",
            logical_call_id="call-fail",
            model="gpt-4o",
            result="error",
            error_kind="network_timeout",
            cost_usd=0.03,
        )
        session.add_all([ok_call, failed_call])
        await session.commit()
        await session.refresh(ok_call)
        await session.refresh(failed_call)

        # 1. facts_for_rows должен учитывать только успешный вызов
        totals = await cost_recount.facts_for_rows(session, [ok_call.id, failed_call.id])
        assert totals.calls == 1
        assert ok_call.id in totals.ref_ids
        assert failed_call.id not in totals.ref_ids
        assert abs(totals.cost_usd - 0.05) < 1e-5

        # 2. _spent_since не должен считать failed_call
        from datetime import datetime, timedelta

        from app.services.step_billing import _Mark

        mark = _Mark(llm_id=0, media_id=0, at=datetime.utcnow() - timedelta(minutes=5))
        spent_total, refs = await _spent_since(session, proj_id, mark)
        assert abs(spent_total - 0.05) < 1e-5
        assert ok_call.id in refs
        assert failed_call.id not in refs


@pytest.mark.asyncio
async def test_step_billing_releases_hold_on_error(tmp_path, monkeypatch) -> None:
    """Проверка: при падении шага холд освобождается и не зависает."""
    from app.settings import settings

    ah.configure(monkeypatch)
    monkeypatch.setattr(settings, "free_tier_enabled", False)
    _engine, factory = await ah.make_engine(tmp_path / "billing-hold.db")
    ah.bind_identity_session(monkeypatch, factory)

    member = await ah.make_account(factory, email="user@studio.local")

    async with factory() as session:
        await credit_ledger.topup(session, member.user_id, amount_micro=50_000_000, memo="test topup")
        proj = Project(slug="hold-test", topic="Hold Test", tenant_id=member.user_id)
        session.add(proj)
        await session.commit()
        await session.refresh(proj)

    # Принудительно устанавливаем tenant для step_billing
    from app.services.tenant import tenant_scope

    with tenant_scope(member.user_id):
        with pytest.raises(RuntimeError, match="network drop simulation"):
            async with step_billing(proj, "img", frames=5):
                raise RuntimeError("network drop simulation")

    # Проверяем, что холд закрыт (released)
    async with factory() as session:
        holds = (
            await session.execute(
                select(credit_ledger.CreditHold).where(credit_ledger.CreditHold.project_id == proj.id)
            )
        ).scalars().all()
        assert len(holds) == 1
        assert holds[0].state == credit_ledger.RELEASED
