"""HITL_AUTO_APPROVE решает карточку сразу, как это сделал бы человек.

Флаг существовал, но влиял только на переход между шагами. Карточки
оставались pending, и шаг «Персонажи» вставал: следующая пара берётся
только из одобренных, а перезапуск шага сносит уже готовых героев.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base, HITLDecision, HITLKind, Project, ProjectStatus
from app.services.hitl import create_hitl


@pytest_asyncio.fixture
async def db(tmp_path, monkeypatch):
    from app.settings import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    (tmp_path / "videos" / "hitl-test").mkdir(parents=True)
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'h.db'}", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        project = Project(topic="t", slug="hitl-test", status=ProjectStatus.generating_hero)
        session.add(project)
        await session.flush()
        yield session, project
    await engine.dispose()


@pytest.mark.asyncio
async def test_card_stays_pending_by_default(db, monkeypatch) -> None:
    from app.settings import settings

    monkeypatch.setattr(settings, "hitl_auto_approve", False)
    session, project = db
    req = await create_hitl(session, project, HITLKind.approve_hero, {"hero_index": 1})
    assert req.decision is HITLDecision.pending
    assert req.decided_at is None


@pytest.mark.asyncio
async def test_flag_approves_on_creation(db, monkeypatch) -> None:
    from app.settings import settings

    monkeypatch.setattr(settings, "hitl_auto_approve", True)
    session, project = db
    req = await create_hitl(session, project, HITLKind.approve_hero, {"hero_index": 1, "variation_index": 1})
    assert req.decision is HITLDecision.approved
    assert req.decided_at is not None


@pytest.mark.asyncio
async def test_approved_hero_pair_counts_as_done(db, monkeypatch) -> None:
    """Ради этого всё и делалось: пара становится одобренной, шаг идёт к следующей."""
    from app.orchestrator.steps.generate_hero import _approved_pairs
    from app.settings import settings

    monkeypatch.setattr(settings, "hitl_auto_approve", True)
    session, project = db
    await create_hitl(session, project, HITLKind.approve_hero, {"hero_index": 1, "variation_index": 1})
    assert await _approved_pairs(session, project) == {(1, 1)}
