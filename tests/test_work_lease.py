"""Тесты D.5: work_leases — атомарный захват, fencing, TTL."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.services.work_lease as wl
from app.models import Base


@pytest_asyncio.fixture
async def lease_db(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'lease.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    @asynccontextmanager
    async def scope():
        async with factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    monkeypatch.setattr(wl, "session_scope", scope)
    yield
    await engine.dispose()


@pytest.mark.asyncio
async def test_acquire_exclusive(lease_db):
    assert await wl.acquire(1, "img:u1", owner="a", ttl_s=60) is True
    # Живой чужой lease не перехватывается.
    assert await wl.acquire(1, "img:u1", owner="b", ttl_s=60) is False
    # Свой — продлевается повторным захватом.
    assert await wl.acquire(1, "img:u1", owner="a", ttl_s=60) is True


@pytest.mark.asyncio
async def test_expired_lease_taken_over(lease_db):
    assert await wl.acquire(1, "img:u1", owner="dead", ttl_s=0.01) is True
    await asyncio.sleep(0.05)
    assert await wl.acquire(1, "img:u1", owner="b", ttl_s=60) is True
    # Прежний владелец больше не может продлить (fencing).
    assert await wl.renew(1, "img:u1", owner="dead") is False


@pytest.mark.asyncio
async def test_renew_only_own_and_alive(lease_db):
    await wl.acquire(1, "video:u2", owner="a", ttl_s=60)
    assert await wl.renew(1, "video:u2", owner="a", ttl_s=60) is True
    assert await wl.renew(1, "video:u2", owner="b") is False
    # Просроченный свой — тоже не продлевается.
    await wl.acquire(2, "video:u3", owner="a", ttl_s=0.01)
    await asyncio.sleep(0.05)
    assert await wl.renew(2, "video:u3", owner="a") is False


@pytest.mark.asyncio
async def test_release_only_own(lease_db):
    await wl.acquire(1, "img:u1", owner="a", ttl_s=60)
    assert await wl.release(1, "img:u1", owner="b") is False
    assert await wl.is_held(1, "img:u1") is True
    assert await wl.release(1, "img:u1", owner="a") is True
    assert await wl.is_held(1, "img:u1") is False


@pytest.mark.asyncio
async def test_concurrent_acquire_exactly_one_wins(lease_db):
    results = await asyncio.gather(*(wl.acquire(1, "img:u1", owner=f"o{i}", ttl_s=60) for i in range(8)))
    assert sum(1 for r in results if r) == 1


@pytest.mark.asyncio
async def test_lease_unit_context(lease_db):
    async with wl.lease_unit(1, "step:split", ttl_s=60) as got:
        assert got is True
        assert await wl.is_held(1, "step:split") is True
    assert await wl.is_held(1, "step:split") is False


@pytest.mark.asyncio
async def test_expire_dead_local_leases(lease_db, monkeypatch):
    import os

    # Живой pid (наш) — не трогается; мёртвый локальный — просрочивается;
    # чужой хост — не трогается.
    await wl.acquire(1, "img:alive", owner=f"{wl._HOST}:{os.getpid()}:aaa", ttl_s=600)
    await wl.acquire(1, "img:dead", owner=f"{wl._HOST}:999999999:bbb", ttl_s=600)
    await wl.acquire(1, "img:remote", owner="other-host:1:ccc", ttl_s=600)
    n = await wl.expire_dead_local_leases()
    assert n == 1
    assert await wl.is_held(1, "img:alive") is True
    assert await wl.is_held(1, "img:dead") is False
    assert await wl.is_held(1, "img:remote") is True


@pytest.mark.asyncio
async def test_lease_unit_second_task_busy(lease_db):
    # Две конкурентные задачи на одну единицу: одна работает, вторая busy.
    started = asyncio.Event()
    release_holder = asyncio.Event()
    outcomes: list[bool] = []

    async def holder():
        async with wl.lease_unit(1, "img:42", ttl_s=60) as got:
            outcomes.append(got)
            started.set()
            await release_holder.wait()

    async def contender():
        await started.wait()
        async with wl.lease_unit(1, "img:42", ttl_s=60) as got:
            outcomes.append(got)
        release_holder.set()

    await asyncio.gather(holder(), contender())
    assert outcomes == [True, False]
    # После выхода держателя единица свободна.
    assert await wl.is_held(1, "img:42") is False


@pytest.mark.asyncio
async def test_task_owner_map_cleaned_after_task(lease_db):
    async def one():
        return wl.current_owner()

    task = asyncio.create_task(one())
    await task
    await asyncio.sleep(0)  # done-callback отрабатывает
    assert id(task) not in wl._task_owner


@pytest.mark.asyncio
async def test_task_owners_differ(lease_db):
    async def owner_of() -> str:
        return wl.current_owner()

    o1, o2 = await asyncio.gather(asyncio.create_task(owner_of()), asyncio.create_task(owner_of()))
    assert o1 != o2
