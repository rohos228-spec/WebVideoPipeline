"""Шесть проектов (5..10) одновременно упираются в один потолок трат и в рубильник.

Ворота ``paid_gate`` — общая точка всех платных вызовов. Здесь проверяется, что
одновременные запросы не проскакивают мимо потолка (проверка и резерв под одним
замком), что потолок проекта режет только свой проект, а рубильник — всех.
Провайдеров нет: только ворота и временная sqlite.
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.models import Base
# На этой линии полей SPEND_*_CAP_USD в Settings нет: spend_guard читает их через getattr.
from app.services import actions_v1 as _actions_v1  # noqa: F401 - action_execution_controls в Base.metadata
from app.services import kill_switch, spend_guard
from app.settings import settings

_PROJECTS = [5, 6, 7, 8, 9, 10]


@pytest.fixture
async def db(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'par_gate.db'}", poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(spend_guard, "_sessions", lambda: sessions)
    import app.db

    monkeypatch.setattr(app.db, "SessionLocal", sessions)
    monkeypatch.setitem(settings.__dict__, "spend_daily_cap_usd", 0.0)
    monkeypatch.setitem(settings.__dict__, "spend_project_cap_usd", 0.0)
    monkeypatch.setitem(settings.__dict__, "spend_run_cap_usd", 0.0)
    from app.services import disk_guard

    monkeypatch.setattr(disk_guard, "check", lambda *a, **k: disk_guard.DiskCheck(True, "ok", "ok"))
    # Замок модуля привязывается к циклу при первой конкуренции; у каждого теста свой цикл.
    monkeypatch.setattr(spend_guard, "_inflight_lock", asyncio.Lock())
    spend_guard.reset_inflight()
    kill_switch.reset_cache()
    yield sessions
    spend_guard.reset_inflight()
    kill_switch.reset_cache()
    await engine.dispose()


async def _gate(project_id: int, usd: float, run_id: str = "") -> object:
    try:
        return await spend_guard.paid_gate(provider="kie", estimate_usd=usd, project_id=project_id, run_id=run_id)
    except spend_guard.PaidCallBlocked as blocked:
        return blocked


def _split(results: list[object]) -> tuple[list[object], list[spend_guard.PaidCallBlocked]]:
    passed = [item for item in results if not isinstance(item, spend_guard.PaidCallBlocked)]
    blocked = [item for item in results if isinstance(item, spend_guard.PaidCallBlocked)]
    return passed, blocked


async def test_daily_cap_lets_exactly_what_fits_through_six_concurrent_projects(db, monkeypatch):
    monkeypatch.setitem(settings.__dict__, "spend_daily_cap_usd", 1.0)
    results = await asyncio.gather(*(_gate(p, 0.3) for p in _PROJECTS))
    passed, blocked = _split(results)
    # 3 * 0.3 = 0.9 входит в 1.0, четвёртый был бы 1.2: ровно три проходят, без гонки.
    assert len(passed) == 3
    assert {item.code for item in blocked} == {"spend_cap_daily"}
    assert spend_guard.inflight_usd()["daily"] == pytest.approx(0.9)
    for ticket in passed:
        ticket.release()
    assert spend_guard.inflight_usd()["count"] == 0


async def test_project_cap_blocks_only_its_project(db, monkeypatch):
    monkeypatch.setitem(settings.__dict__, "spend_project_cap_usd", 0.5)
    first = await spend_guard.paid_gate(provider="kie", estimate_usd=0.4, project_id=7)
    results = await asyncio.gather(*(_gate(p, 0.3) for p in _PROJECTS))
    blocked_projects = [p for p, item in zip(_PROJECTS, results) if isinstance(item, spend_guard.PaidCallBlocked)]
    assert blocked_projects == [7]
    assert results[_PROJECTS.index(7)].code == "spend_cap_project"
    first.release()
    for item in results:
        if not isinstance(item, spend_guard.PaidCallBlocked):
            item.release()


async def test_run_cap_is_per_run_not_shared_between_projects(db, monkeypatch):
    monkeypatch.setitem(settings.__dict__, "spend_run_cap_usd", 0.5)
    results = await asyncio.gather(*(_gate(p, 0.3, run_id=f"run-{p}") for p in _PROJECTS))
    passed, blocked = _split(results)
    assert len(passed) == 6 and not blocked
    for ticket in passed:
        ticket.release()
    shared = await asyncio.gather(*(_gate(p, 0.3, run_id="one-run") for p in _PROJECTS))
    passed, blocked = _split(shared)
    assert len(passed) == 1
    assert {item.code for item in blocked} == {"spend_cap_run"}
    for ticket in passed:
        ticket.release()


async def test_kill_switch_blocks_all_six_at_once(db):
    await kill_switch.set_state(db, active=True, actor="test-operator", rationale="parallel stop test")
    results = await asyncio.gather(*(_gate(p, 0.1) for p in _PROJECTS))
    passed, blocked = _split(results)
    assert not passed
    assert {item.code for item in blocked} == {spend_guard.BLOCK_KILL_SWITCH}
    await kill_switch.set_state(db, active=False, actor="test-operator", rationale="parallel stop test off")
    kill_switch.reset_cache()
    results = await asyncio.gather(*(_gate(p, 0.1) for p in _PROJECTS))
    passed, blocked = _split(results)
    assert len(passed) == 6
    for ticket in passed:
        ticket.release()
