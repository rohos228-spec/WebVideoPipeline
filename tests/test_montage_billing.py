"""Касса вокруг board-операций v2: варианты сцен и фоновый regen.

Дырка, которую закрывают эти тесты: v2-операции жгут провайдерские вызовы
(LLM-варианты, медиа-regen) мимо конвейерных шагов, где стоит касса.
Обёртки `step_billing` стоят на choke-point'ах:

* `POST .../scene-variants` — синхронный LLM-вызов (`scene_variant`);
* фоновый `spawn_apply_job` — regen очереди (`montage_regen`, tenant уже
  захвачен через `tenant_scope` в джобе).

Оба списывают по журналам (факт × маржа, цена уточнится данными
владельца — TODO(reprice)); trim/insert/merge/refs денег не тратят и не
обёрнуты сознательно.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest
import pytest_asyncio

from app.models import CreditEntry, LlmCall, MediaCall, Project, ProjectStatus
from app.services import credit_ledger as cl
from app.services import step_billing as sb
from app.services.credits import price_micro
from app.services.tenant import tenant_scope
from tests import accounts_harness as ah


@pytest_asyncio.fixture
async def env(tmp_path, monkeypatch):
    ah.configure(monkeypatch)
    engine, factory = await ah.make_engine(tmp_path / "boardbill.db")
    ah.bind_identity_session(monkeypatch, factory)

    @asynccontextmanager
    async def _scope():
        async with factory() as s:
            yield s
            await s.commit()

    # Касса и фоновый джоб открывают сессии сами — направляем в тестовую базу.
    monkeypatch.setattr(sb, "session_scope", _scope)
    import app.services.montage_board_apply_job as job_mod

    monkeypatch.setattr(job_mod, "session_scope", _scope)

    member = await ah.make_account(factory, email="user@studio.local", role="member")
    async with factory() as s:
        s.add(Project(slug="board", topic="t", status=ProjectStatus.new, pipeline_mode="v2"))
        await s.commit()
    yield {"factory": factory, "member": member, "job_mod": job_mod}
    await engine.dispose()


async def _fund_and_exit_free_tier(factory, tenant: str, project_id: int, amount_micro: int) -> None:
    """Пополнить и отметить оплаченное видео — дальше идёт платный путь кассы."""
    async with factory() as s:
        await cl.topup(s, tenant, amount_micro)
        hold = await cl.open_hold(s, tenant, project_id=project_id, step_code="video", amount_micro=1_000_000)
        await cl.settle_hold(s, hold.id, cost_usd=0.1, ref_table="", ref_ids=[])
        await s.commit()


async def _balance(factory, tenant: str) -> int:
    async with factory() as s:
        return await cl.balance_micro(s, tenant)


async def _settle_entries(factory, tenant: str, charged: int) -> list:
    """Проводки списания ровно на эту сумму (seed-видео отсекается суммой)."""
    from sqlalchemy import select

    async with factory() as s:
        rows = (
            (
                await s.execute(
                    select(CreditEntry).where(
                        CreditEntry.tenant_id == tenant,
                        CreditEntry.kind == "settle",
                        CreditEntry.delta_micro == -charged,
                    )
                )
            )
            .scalars()
            .all()
        )
        return list(rows)


@pytest.mark.asyncio
async def test_scene_variants_bills_llm_journal(env, monkeypatch) -> None:
    """Варианты сцен: журнальный вызов оплачивается членом (не дарится)."""
    from app.web.routers import project_ops as po

    factory = env["factory"]
    tenant = env["member"].user_id
    async with factory() as s:
        from sqlalchemy import select

        pid = (await s.execute(select(Project.id).where(Project.slug == "board"))).scalar_one()
    await _fund_and_exit_free_tier(factory, tenant, pid, 50_000_000)

    async def fake_state(session, project, frame_id):
        return {"frame": frame_id}

    async def fake_variants(state, *, kind="action", desc="", count=3, project_id=None):
        async with factory() as s:
            s.add(LlmCall(project_id=pid, logical_call_id="t", model="m", cost_usd=0.5))
            await s.commit()
        return {"ok": True, "variants": []}

    monkeypatch.setattr(po, "_scene_editor_state", fake_state)
    monkeypatch.setattr("app.services.montage_scene_editor.generate_scene_variants", fake_variants)

    before = await _balance(factory, tenant)
    async with factory() as s:
        with tenant_scope(tenant):
            res = await po.montage_board_scene_variants(pid, 1, {"kind": "action"}, s)
    assert res["ok"] is True

    charged = price_micro(0.5)
    assert await _balance(factory, tenant) == before - charged
    entries = await _settle_entries(factory, tenant, charged)
    assert len(entries) == 1


@pytest.mark.asyncio
async def test_apply_job_bills_media_regen(env, monkeypatch) -> None:
    """Фоновый regen: медиа-вызовы очереди списываются, а не дарятся."""
    from sqlalchemy import select

    job_mod = env["job_mod"]
    factory = env["factory"]
    tenant = env["member"].user_id
    async with factory() as s:
        pid = (await s.execute(select(Project.id).where(Project.slug == "board"))).scalar_one()
    await _fund_and_exit_free_tier(factory, tenant, pid, 50_000_000)

    async def fake_apply(session, project, *, video_trims=None, pending_ops=None, on_progress=None):
        async with factory() as s:
            s.add(
                MediaCall(
                    project_id=pid,
                    provider="outsee",
                    kind="video",
                    model="m",
                    units=1.0,
                    cost_usd=0.19,
                )
            )
            await s.commit()
        return {"ok": True, "errors": [], "results": []}

    async def fake_publish(*args, **kwargs):
        return None

    monkeypatch.setattr(job_mod, "apply_montage_board", fake_apply)
    monkeypatch.setattr(job_mod, "_publish", fake_publish)

    before = await _balance(factory, tenant)
    with tenant_scope(tenant):
        task = job_mod.spawn_apply_job(pid, video_trims=None, pending_ops=[{"type": "regen"}])
        await task
    job_mod._apply_tasks.clear()

    charged = price_micro(0.19)
    assert await _balance(factory, tenant) == before - charged
    entries = await _settle_entries(factory, tenant, charged)
    assert len(entries) == 1
