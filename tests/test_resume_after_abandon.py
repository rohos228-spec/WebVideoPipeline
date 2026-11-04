"""Сдача шага после повторов ошибок: «снять паузу» / «продолжить» не теряет место проекта.

Как в #10: 9 сбоев generating_image_prompts → paused без paused_from_status, «продолжить»
откатывал проект в new.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base, Project, ProjectStatus
from app.orchestrator.auto_advance import continue_project_pipeline
from app.services.project_control import resume_project
from app.services.step_failure_policy import (
    MAX_TOTAL_FAILS,
    ready_status_before,
    record_step_failure,
    resume_place_without_origin,
)

# meta #10 (без лишнего): сдача на промтах картинок, ранее стоп запуска на enrich_3_ready.
LEGACY_10 = {
    "startup_autorun_blocked": True,
    "startup_blocked_ready_status": "enrich_3_ready",
    "step_failure": {
        "total_fails": {"generating_image_prompts": 9},
        "last_running": "generating_image_prompts",
        "wait_profile": "safe",
        "recovery_cycles": 3,
        "abandoned_at": "2026-10-22T10:00:00+00:00",
        "abandoned_reason": "retries_exhausted",
        "abandoned_threshold": 9,
    },
}


@pytest.fixture
async def session() -> AsyncSession:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


async def _project(session, *, status=ProjectStatus.paused, meta=None, auto_mode=False, slots=3) -> Project:
    p = Project(
        slug=f"p{id(meta)}",
        topic="t",
        status=status,
        auto_mode=auto_mode,
        enrich_slots_count=slots,
        meta=dict(meta or {}),
    )
    session.add(p)
    await session.commit()
    return p


def test_ready_status_before_follows_project_enrich_slots() -> None:
    three = Project(slug="a", topic="t", enrich_slots_count=3)
    five = Project(slug="b", topic="t", enrich_slots_count=5)
    run = ProjectStatus.generating_image_prompts
    assert ready_status_before(three, run) is ProjectStatus.enrich_3_ready
    assert ready_status_before(five, run) is ProjectStatus.enrich_5_ready
    assert ready_status_before(three, ProjectStatus.generating_images) is ProjectStatus.image_prompts_ready
    assert ready_status_before(three, ProjectStatus.enriching_2) is ProjectStatus.enrich_1_ready
    # У сценария нет требования - место «новый», это не потеря места.
    assert ready_status_before(three, ProjectStatus.planning) is ProjectStatus.new
    assert ready_status_before(three, ProjectStatus.enrich_3_ready) is None


async def test_abandon_records_ready_status_before_failed_step(session) -> None:
    p = await _project(
        session,
        status=ProjectStatus.generating_image_prompts,
        meta={"step_failure": {"total_fails": {"generating_image_prompts": MAX_TOTAL_FAILS - 1}}},
    )
    with (
        patch("app.services.run_sync.mark_running_node_failed", new_callable=AsyncMock),
        patch("app.services.step_failure_policy._soft_retry_without_wipe", new_callable=AsyncMock),
    ):
        action = await record_step_failure(session, p, error=RuntimeError("промты не заполнены"))
    assert action == "abandon"
    assert p.status is ProjectStatus.paused
    fs = p.meta["step_failure"]
    # На этой линии причины сдачи (abandoned_reason) нет: сдача - это abandoned_at.
    assert fs.get("abandoned_at")
    # Не сам шаг (воркер сразу перезапустил бы платный шаг) и не new.
    assert p.meta["paused_from_status"] == "enrich_3_ready"


async def test_abandoned_project_resumes_to_ready_and_does_not_run(session) -> None:
    p = await _project(
        session,
        status=ProjectStatus.generating_image_prompts,
        meta={"step_failure": {"total_fails": {"generating_image_prompts": MAX_TOTAL_FAILS - 1}}},
    )
    with (
        patch("app.services.run_sync.mark_running_node_failed", new_callable=AsyncMock),
        patch("app.services.step_failure_policy._soft_retry_without_wipe", new_callable=AsyncMock),
    ):
        await record_step_failure(session, p, error=RuntimeError("промты не заполнены"))
    assert await resume_project(session, p) == "enrich_3_ready"
    assert "paused_from_status" not in p.meta


@pytest.mark.parametrize("auto_mode", [False, True])
async def test_continue_on_legacy_abandoned_project_keeps_its_place(session, auto_mode) -> None:
    p = await _project(session, meta=LEGACY_10, auto_mode=auto_mode)
    info = await continue_project_pipeline(session, p, bot=None)
    assert info == {"action": "resumed", "status": "enrich_3_ready", "advanced": False}
    assert p.status is ProjectStatus.enrich_3_ready
    # Платный шаг сам из «продолжить» не стартует: место - готовность перед ним.
    assert p.status is not ProjectStatus.generating_image_prompts


async def test_resume_prefers_recorded_origin(session) -> None:
    meta = {**LEGACY_10, "paused_from_status": "generating_images"}
    p = await _project(session, meta=meta)
    assert await resume_project(session, p) == "generating_images"


async def test_resume_without_any_trace_still_goes_to_new(session) -> None:
    p = await _project(session, meta={})
    assert await resume_project(session, p) == "new"


def test_place_without_origin_ignores_stale_failure_trace() -> None:
    # Сбой давно прошёл (total_fails снят успехом), last_running остался - берём стоп запуска.
    p = Project(
        slug="s",
        topic="t",
        enrich_slots_count=3,
        meta={
            "startup_blocked_ready_status": "image_prompts_ready",
            "step_failure": {"last_running": "generating_hero", "total_fails": {}},
        },
    )
    assert resume_place_without_origin(p) is ProjectStatus.image_prompts_ready
    # Без стопа запуска - готовность перед последним шагом лучше, чем new.
    p.meta = {"step_failure": {"last_running": "generating_hero"}}
    assert resume_place_without_origin(p) is ProjectStatus.frames_ready
    # Сон после ошибок / счётчик шага - след свежий.
    p.meta = {
        "startup_blocked_ready_status": "frames_ready",
        "step_failure": {"last_running": "generating_images", "total_fails": {"generating_images": 3}},
    }
    assert resume_place_without_origin(p) is ProjectStatus.image_prompts_ready
    # Running-статус в стопе запуска - не место.
    p.meta = {"startup_blocked_ready_status": "generating_images"}
    assert resume_place_without_origin(p) is None
