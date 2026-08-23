"""Касса вокруг шага: резерв, списание по факту, возврат при падении.

Главная проверка — первая: в режиме владельца касса не должна делать
ничего. Живой конвейер работает без арендатора, и любое обращение к счёту
там означало бы, что переход на SaaS сломал работающую систему.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from app.db import session_scope
from app.models import LlmCall, MediaCall, Project
from app.services import credit_ledger as cl
from app.services.credits import price_micro
from app.services.step_billing import step_billing
from app.services.tenant import tenant_scope


def _project(**over) -> SimpleNamespace:
    base = dict(
        id=1,
        meta=None,
        image_resolution=None,
        aspect_ratio=None,
        image_quality=None,
        video_resolution=None,
        video_duration=6,
        image_generator_id=None,
        video_generator_id=None,
    )
    base.update(over)
    return SimpleNamespace(**base)


@pytest.fixture(autouse=True)
def _ledger_without_rls(monkeypatch) -> None:
    """Арендатор как ключ учёта, а не как граница безопасности.

    Механика кассы — сходимость баланса, возврат резерва, граница журнала —
    от RLS не зависит вовсе. Требовать ради неё живой Postgres значило бы
    не иметь возможности прогнать суиту на машине без него. Сам запрет
    многоарендного режима на SQLite проверяется отдельно, в
    `test_tenant_isolation.py`.
    """
    from app.settings import settings

    monkeypatch.setattr(settings, "allow_unisolated_tenants", True)


@pytest.fixture
def no_history(monkeypatch) -> None:
    from app.services import quote as q

    async def _empty(step_code, *, session=None, limit=50):  # noqa: ARG001
        return []

    monkeypatch.setattr(q, "step_history_usd", _empty)


async def _funded_tenant(amount_micro: int = 50 * 10**6) -> str:
    tenant = str(uuid.uuid4())
    async with session_scope() as s:
        await cl.topup(s, tenant, amount_micro)
    return tenant


async def test_owner_mode_touches_nothing(no_history) -> None:
    """Без арендатора касса выключена целиком.

    Не заглушка: у владельца кредитов нет вовсе, он платит провайдерам
    напрямую. Конвейер обязан вести себя ровно как до появления кассы.
    """
    from sqlalchemy import func, select

    from app.models import CreditEntry, CreditHold

    async with step_billing(_project(), "video", frames=24) as bill:
        assert not bill.billed
        assert bill.tenant_id is None

    async with session_scope() as s:
        holds = (await s.execute(select(func.count()).select_from(CreditHold))).scalar_one()
        entries = (await s.execute(select(func.count()).select_from(CreditEntry))).scalar_one()
    assert (holds, entries) == (0, 0)


async def test_hold_is_taken_and_settled_by_fact(no_history) -> None:
    """Резерв по p90, списание по журналам вызовов."""
    tenant = await _funded_tenant()
    async with session_scope() as s:
        project = Project(slug=f"p-{uuid.uuid4().hex[:8]}", topic="тест")
        s.add(project)
        await s.flush()
        project_id = project.id

    with tenant_scope(tenant):
        async with session_scope() as s:
            balance_before = await cl.balance_micro(s, tenant)

        async with step_billing(_project(id=project_id), "video", frames=24) as bill:
            assert bill.billed
            assert bill.held_micro > 0
            # Шаг «потратил»: две генерации по $0.19.
            async with session_scope() as s:
                for _ in range(2):
                    s.add(
                        MediaCall(
                            project_id=project_id,
                            node_key="videos",
                            provider="minimax",
                            kind="video",
                            model="MiniMax-Hailuo-2.3-Fast",
                            units=6.0,
                            unit="second",
                            cost_usd=0.19,
                        )
                    )

        assert bill.cost_usd == pytest.approx(0.38)
        assert bill.charged_micro == price_micro(0.38)

        async with session_scope() as s:
            cached, computed = await cl.reconcile(s, tenant)
            assert cached == computed
            assert cached == balance_before - price_micro(0.38)


async def test_failed_step_returns_the_whole_hold(no_history) -> None:
    """Клиент не платит ни за одну неудачную попытку."""
    tenant = await _funded_tenant()
    async with session_scope() as s:
        before = await cl.balance_micro(s, tenant)

    with tenant_scope(tenant), pytest.raises(RuntimeError, match="провайдер отказал"):
        async with step_billing(_project(id=999), "video", frames=24):
            raise RuntimeError("провайдер отказал")

    async with session_scope() as s:
        cached, computed = await cl.reconcile(s, tenant)
    assert cached == computed == before


async def test_only_this_project_is_charged(no_history) -> None:
    """Расход соседнего проекта не попадает в чужой счёт."""
    tenant = await _funded_tenant()
    async with session_scope() as s:
        mine = Project(slug=f"mine-{uuid.uuid4().hex[:8]}", topic="мой")
        other = Project(slug=f"other-{uuid.uuid4().hex[:8]}", topic="чужой")
        s.add_all([mine, other])
        await s.flush()
        mine_id, other_id = mine.id, other.id

    with tenant_scope(tenant):
        async with step_billing(_project(id=mine_id), "img_pr") as bill:
            async with session_scope() as s:
                s.add(LlmCall(project_id=mine_id, node_key="image_prompts", logical_call_id="a",
                              model="m", cost_usd=0.10))
                s.add(LlmCall(project_id=other_id, node_key="image_prompts", logical_call_id="b",
                              model="m", cost_usd=9.99))
        assert bill.cost_usd == pytest.approx(0.10)


async def test_spend_before_the_step_is_not_recharged(no_history) -> None:
    """Граница журнала берётся по id, а не по времени.

    Иначе повторный запуск шага списал бы заново то, за что уже заплачено.
    """
    tenant = await _funded_tenant()
    async with session_scope() as s:
        project = Project(slug=f"old-{uuid.uuid4().hex[:8]}", topic="тест")
        s.add(project)
        await s.flush()
        project_id = project.id
        s.add(LlmCall(project_id=project_id, node_key="image_prompts", logical_call_id="old",
                      model="m", cost_usd=5.00))

    with tenant_scope(tenant):
        async with step_billing(_project(id=project_id), "img_pr") as bill:
            async with session_scope() as s:
                s.add(LlmCall(project_id=project_id, node_key="image_prompts",
                              logical_call_id="new", model="m", cost_usd=0.02))
        assert bill.cost_usd == pytest.approx(0.02)


async def test_insufficient_balance_stops_the_step(no_history) -> None:
    """Денег нет — шаг не стартует, а не падает на середине."""
    tenant = await _funded_tenant(1_000)  # 0.001 кредита
    with tenant_scope(tenant), pytest.raises(cl.InsufficientCredits):
        async with step_billing(_project(id=1), "video", frames=24):
            pytest.fail("шаг не должен был начаться")
