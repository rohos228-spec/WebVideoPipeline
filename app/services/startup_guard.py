"""Startup safety guard: never continue old pipeline work automatically."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import BatchProject, BatchStatus, Project, ProjectStatus
from app.services.project_state import is_running_status



async def block_pipeline_autorun_on_startup(session: AsyncSession) -> dict[str, Any]:
    """Rollback running work on restart — but keep user's auto_mode preference.

    After process restart, in-flight steps roll back to *_ready and wait for
    explicit ▶. ``auto_mode`` is NOT cleared: user turned it on intentionally;
    ``auto_await_manual_start`` blocks autostart until ▶ (see project_control).
    """
    from app.orchestrator.auto_advance import TRANSITIONS
    from app.services.mass_pause import set_active as set_mass_pause
    from app.services.project_control import arm_auto_await_manual_start

    now = datetime.utcnow().isoformat(timespec="seconds")
    stats: dict[str, Any] = {
        "running_projects_rolled_back": 0,
        "auto_mode_disabled": 0,  # legacy counter — always 0 (auto_mode preserved)
        "auto_await_manual_armed": 0,
        "batches_paused": 0,
        "mass_pause_enabled": False,
    }

    # Этап 2 (E.1): lease мёртвых pid этой машины → просроченные, чтобы
    # resume не ждал полного TTL.
    try:
        from app.services.work_lease import expire_dead_local_leases

        await expire_dead_local_leases()
    except Exception:  # noqa: BLE001
        logger.debug("STARTUP GUARD: expire_dead_local_leases failed", exc_info=True)

    projects = (await session.execute(select(Project))).scalars().all()
    ready_statuses = set(TRANSITIONS.keys())

    for project in projects:
        meta = dict(project.meta or {})
        changed = False

        if is_running_status(project.status):
            # Этап 2 (E.1): рестарт БОЛЬШЕ НЕ откатывает статус и не
            # сбрасывает NodeRun («рестарт = откат» закрыт). Проект
            # помечается осиротевшим; политика «не продолжать старую
            # работу автоматически» сохраняется через auto_await — по ▶
            # шаг стартует с ТОГО ЖЕ running-статуса и доезжает с курсора
            # (чекпоинты C.*); recovery-скан диск→БД идёт до продолжения.
            meta["orphaned_running"] = True
            meta["orphaned_running_at"] = now
            meta["orphaned_running_status"] = project.status.value
            meta["startup_autorun_blocked"] = True
            meta["startup_blocked_at"] = now
            # Legacy-метки отката больше не пишутся.
            meta.pop("startup_blocked_running_status", None)
            meta.pop("startup_rollback_to", None)
            meta.pop("enrich_auto_chain_to", None)
            # Не гасим auto_mode — только ждём ручной ▶.
            meta.pop("startup_auto_mode_disabled", None)
            project.meta = meta
            if arm_auto_await_manual_start(project):
                stats["auto_await_manual_armed"] += 1
            meta = dict(project.meta or {})
            stats["running_projects_rolled_back"] += 1
            changed = True
            logger.warning(
                "[#{}] STARTUP GUARD: {} осиротел (статус сохранён, "
                "resume с курсора по ▶; auto_mode={} сохранён)",
                project.id,
                project.status.value,
                project.auto_mode,
            )

        elif project.auto_mode and project.status in ready_statuses:
            # assembled/published — пайплайн уже у финала; не ставим «ждём ▶»
            # (иначе на каждом рестарте снова auto_await + шум в worker).
            if project.status in (
                ProjectStatus.assembled,
                ProjectStatus.publishing,
                ProjectStatus.published,
            ):
                continue
            # auto_mode оставляем; без ▶ ничего не стартует.
            meta["startup_autorun_blocked"] = True
            meta["startup_blocked_at"] = now
            meta["startup_blocked_ready_status"] = project.status.value
            meta.pop("startup_auto_mode_disabled", None)
            project.meta = meta
            if arm_auto_await_manual_start(project):
                stats["auto_await_manual_armed"] += 1
            changed = True
            logger.info(
                "[#{}] STARTUP GUARD: auto_mode сохранён at {} — ждём ▶",
                project.id,
                project.status.value,
            )

        if changed:
            project.meta = dict(project.meta or {})
            project.updated_at = datetime.utcnow()

    batches = (
        (await session.execute(select(BatchProject).where(BatchProject.status == BatchStatus.running)))
        .scalars()
        .all()
    )
    for batch in batches:
        batch.status = BatchStatus.paused
        batch.updated_at = datetime.utcnow()
        stats["batches_paused"] += 1
        logger.warning("[batch #{}] STARTUP GUARD: running -> paused", batch.id)

    if stats["batches_paused"]:
        set_mass_pause(True)
        stats["mass_pause_enabled"] = True

    if any(
        stats[key]
        for key in (
            "running_projects_rolled_back",
            "auto_await_manual_armed",
            "batches_paused",
        )
    ):
        await session.flush()

    return stats
