"""Старт шага: отпечаток промта, защита слота и явный node_key.

Три находки живого прогона 2026-08-31 в одной точке входа:

* п.11 — в графе лежит имя варианта, а текст вне git; расхождение надо
  называть вслух (`prompt_drift`);
* п.3 — узел вне слотов 1..5 ронял `KeyError: 0`, причём ПОСЛЕ того, как
  выходы шага уже стёрты;
* п.20 — явный `node_key` терялся на кодах `enrich_N`, и исполнялся чужой узел.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base, Project, ProjectStatus, Workflow
from app.services.project_steps import start_step


@pytest.fixture
async def mem_db(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    from app import settings as app_settings

    monkeypatch.setattr(app_settings.settings, "data_dir", tmp_path / "data")
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    @asynccontextmanager
    async def _scope():
        async with factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    monkeypatch.setattr("app.db.session_scope", _scope)
    monkeypatch.setattr("app.services.run_sync.session_scope", _scope)
    yield _scope
    await engine.dispose()


def _canvas(nodes: list[dict]) -> dict:
    return {"canvas_graph": {"workflow_id": 1, "nodes": nodes, "edges": []}}


async def _project(session, meta: dict, status=ProjectStatus.frames_ready) -> Project:
    wf = Workflow(name=f"wf-{uuid.uuid4().hex[:8]}", is_default=True, nodes=[], edges=[])
    session.add(wf)
    await session.flush()
    p = Project(
        slug=f"p-{uuid.uuid4().hex[:8]}",
        topic="тема",
        status=status,
        meta=meta,
    )
    session.add(p)
    await session.flush()
    return p


@pytest.mark.asyncio
async def test_overflow_node_is_refused_before_outputs_are_wiped(mem_db) -> None:
    """Отказ обязан прийти ДО стирания: раньше падало после wipe."""
    async with mem_db() as session:
        node = {"id": "n_check", "type": "excel_gpt", "data": {"slotOverflow": True}}
        p = await _project(session, _canvas([node]))
        calls = {"wipe": 0}

        import app.services.project_steps as ps

        async def _spy(*_a, **_k):
            calls["wipe"] += 1
            return {}

        original = ps.clear_step_outputs_for_rerun
        ps.clear_step_outputs_for_rerun = _spy
        try:
            with pytest.raises(ValueError, match="вне слотов"):
                await start_step(session, p, "excel_gpt", node_key="n_check")
        finally:
            ps.clear_step_outputs_for_rerun = original
        assert calls["wipe"] == 0


@pytest.mark.asyncio
async def test_explicit_node_key_wins_on_enrich_step(mem_db) -> None:
    """Запрошен слот 2 — активной становится названная нода, а не прежняя."""
    async with mem_db() as session:
        nodes = [
            {"id": "n_a", "type": "excel_gpt", "data": {"slotIndex": 1}},
            {"id": "n_b", "type": "excel_gpt", "data": {"slotIndex": 2}},
        ]
        meta = _canvas(nodes)
        meta["active_excel_gpt_node_key"] = "n_a"
        p = await _project(session, meta, status=ProjectStatus.hero_ready)
        await start_step(session, p, "enrich_2", node_key="n_b")
        assert (p.meta or {}).get("active_excel_gpt_node_key") == "n_b"


@pytest.mark.asyncio
async def test_prompt_fingerprint_is_recorded_on_start(mem_db, tmp_path, monkeypatch) -> None:
    """После старта у шага есть отпечаток промта — иначе дрейф не заметить."""
    from app.services.prompt_drift import META_KEY

    # Настоящая prompts/ вне git: на чистом клоне (CI) чтение промта падает,
    # отпечаток молча не снимается и тест краснеет данными, а не кодом.
    (tmp_path / "01_plan").mkdir()
    (tmp_path / "01_plan" / "default.md").write_text("# план", encoding="utf-8")
    monkeypatch.setattr("app.services.prompt_library.PROMPTS_ROOT", tmp_path)

    async with mem_db() as session:
        p = await _project(session, _canvas([{"id": "n_plan", "type": "plan"}]), status=ProjectStatus.new)
        await start_step(session, p, "plan", node_key="n_plan")
        rec = (p.meta or {}).get(META_KEY, {}).get("n_plan")
        assert rec and rec["step"] == "plan" and rec["hash"]


@pytest.mark.asyncio
async def test_broken_fingerprint_does_not_block_the_start(mem_db, monkeypatch) -> None:
    """Отпечаток — не повод не запускать шаг: его падение глотается в лог."""

    def _boom(*_a, **_k):
        raise RuntimeError("ledger сломан")

    monkeypatch.setattr("app.services.prompt_drift.note_prompt_used", _boom)
    async with mem_db() as session:
        p = await _project(session, _canvas([{"id": "n_plan", "type": "plan"}]), status=ProjectStatus.new)
        await start_step(session, p, "plan", node_key="n_plan")  # не подняло
        assert p.status is not ProjectStatus.new
