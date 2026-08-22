"""Лизинги единиц работы: TTL + owner + fencing (этап 2, cache-resume).

Заменяет голые INFLIGHT-маркеры (`img_gen_inflight`/`video_gen_inflight`)
и in-process `_SPLIT_LOCK`: упавший процесс отдаёт единицу по TTL, два
процесса/задачи не генерят один кадр дважды.

Правила (спека + панель 3/3):

- захват — ОДИН атомарный ``INSERT … ON CONFLICT … DO UPDATE … WHERE
  expires_at < :now`` с проверкой rowcount; никаких read-then-write;
- release/renew — ТОЛЬКО со сверкой владельца (``WHERE owner = :me``);
  rowcount=0 на renew = lease потерян → задача прекращает единицу и НЕ
  публикует результат;
- каждая операция — в собственном коротком session_scope с немедленным
  commit (не внутри долгой сессии шага: NullPool держал бы writer-lock);
- owner = host:pid:task_uuid — различает asyncio-задачи одного процесса.

Использование:

    async with lease_unit(project_id, f"img:{frame.uuid}", ttl_s=900) as ok:
        if not ok:            # кем-то занят (живой lease)
            return
        ... работа ...
        # перед публикацией результата:
        if not await renew(project_id, unit_key, owner=current_owner()): ...
"""

from __future__ import annotations

import asyncio
import os
import socket
import time
import uuid as _uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from loguru import logger
from sqlalchemy import delete, text, update

from app.db import session_scope
from app.models import WorkLease

# TTL по типу единицы (сек); переопределяется параметром.
DEFAULT_TTL_S = {
    "img": 15 * 60,
    "video": 30 * 60,
    "step": 60 * 60,
}

_HOST = socket.gethostname()

# owner текущей asyncio-задачи (uuid на задачу, лениво).
_task_owner: dict[int, str] = {}

_LOCK_RETRIES = 3
_LOCK_RETRY_SLEEP_S = 0.5


def current_owner() -> str:
    """host:pid:task_uuid — стабилен внутри одной asyncio-задачи."""
    try:
        task = asyncio.current_task()
        key = id(task) if task is not None else 0
    except RuntimeError:
        task = None
        key = 0
    owner = _task_owner.get(key)
    if owner is None:
        owner = f"{_HOST}:{os.getpid()}:{_uuid.uuid4().hex[:12]}"
        _task_owner[key] = owner
        # Ревью [4/4]: без чистки словарь растёт вечно, а переиспользование
        # id() после GC отдавало бы owner мёртвой задачи новой.
        if task is not None:
            task.add_done_callback(lambda t: _task_owner.pop(id(t), None))
    return owner


def _ttl_for(unit_key: str, ttl_s: float | None) -> float:
    if ttl_s is not None:
        return float(ttl_s)
    prefix = unit_key.split(":", 1)[0]
    return float(DEFAULT_TTL_S.get(prefix, DEFAULT_TTL_S["step"]))


async def _ensure_table() -> None:
    """Таблица создаётся create_all на старте; для БД, поднятых до этапа 2
    (и голых тестовых engine'ов), — ленивое создание."""
    from app.db import engine

    async with engine.begin() as conn:
        await conn.run_sync(
            lambda c: WorkLease.__table__.create(c, checkfirst=True)  # type: ignore[attr-defined]
        )


async def _execute_with_retry(stmt, params=None) -> int:
    """Короткая собственная сессия + ретраи на database is locked."""
    last_exc: Exception | None = None
    table_ensured = False
    for attempt in range(1, _LOCK_RETRIES + 1):
        try:
            async with session_scope() as session:
                res = await session.execute(stmt, params or {})
                return int(res.rowcount or 0)  # type: ignore[attr-defined]
        except Exception as e:  # noqa: BLE001
            msg = str(e).lower()
            if "no such table" in msg and not table_ensured:
                table_ensured = True
                await _ensure_table()
                continue
            if "database is locked" not in msg:
                raise
            last_exc = e
            await asyncio.sleep(_LOCK_RETRY_SLEEP_S * attempt)
    raise RuntimeError(f"work_lease: database is locked ×{_LOCK_RETRIES}") from last_exc


async def acquire(
    project_id: int,
    unit_key: str,
    *,
    owner: str | None = None,
    ttl_s: float | None = None,
) -> bool:
    """Атомарный захват; True — единица наша, False — занята живым lease."""
    me = owner or current_owner()
    now = time.time()
    expires = now + _ttl_for(unit_key, ttl_s)
    # ON CONFLICT … WHERE: перехват только просроченного (или своего) lease.
    stmt = text(
        """
        INSERT INTO work_leases (project_id, unit_key, owner, expires_at,
                                 created_at, updated_at)
        VALUES (:pid, :key, :me, :exp, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        ON CONFLICT (project_id, unit_key) DO UPDATE
            SET owner = :me, expires_at = :exp,
                updated_at = CURRENT_TIMESTAMP
            WHERE work_leases.expires_at < :now
               OR work_leases.owner = :me
        """
    )
    rowcount = await _execute_with_retry(
        stmt, {"pid": project_id, "key": unit_key, "me": me, "exp": expires, "now": now}
    )
    ok = rowcount > 0
    if not ok:
        logger.debug("work_lease: {}/{} занят живым lease — пропуск", project_id, unit_key)
    return ok


async def renew(
    project_id: int,
    unit_key: str,
    *,
    owner: str | None = None,
    ttl_s: float | None = None,
) -> bool:
    """Продление СВОЕГО живого lease. False = lease потерян: немедленно
    прекратить единицу работы и не публиковать результат (fencing)."""
    me = owner or current_owner()
    now = time.time()
    stmt = (
        update(WorkLease)
        .where(
            WorkLease.project_id == project_id,
            WorkLease.unit_key == unit_key,
            WorkLease.owner == me,
            WorkLease.expires_at > now,
        )
        .values(expires_at=now + _ttl_for(unit_key, ttl_s))
    )
    ok = (await _execute_with_retry(stmt)) > 0
    if not ok:
        logger.warning(
            "work_lease: renew {}/{} FAILED — lease потерян (owner={})",
            project_id,
            unit_key,
            me,
        )
    return ok


async def release(project_id: int, unit_key: str, *, owner: str | None = None) -> bool:
    """Освобождение ТОЛЬКО своего lease (чужой не трогаем — fencing)."""
    me = owner or current_owner()
    stmt = delete(WorkLease).where(
        WorkLease.project_id == project_id,
        WorkLease.unit_key == unit_key,
        WorkLease.owner == me,
    )
    try:
        return (await _execute_with_retry(stmt)) > 0
    except Exception as e:  # noqa: BLE001 — release не должен ронять шаг
        logger.warning("work_lease: release {}/{}: {}", project_id, unit_key, e)
        return False


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True  # PermissionError и т.п. — процесс есть
    return True


async def expire_dead_local_leases() -> int:
    """Lease'ы мёртвых pid ЭТОЙ машины → просроченные (startup: перехват
    осиротевших единиц без ожидания полного TTL)."""
    from sqlalchemy import select

    now = time.time()
    expired = 0
    async with session_scope() as session:
        rows = (await session.execute(select(WorkLease))).scalars().all()
        for lease_row in rows:
            if float(lease_row.expires_at) <= now:
                continue
            parts = (lease_row.owner or "").split(":")
            if len(parts) < 3 or parts[0] != _HOST:
                continue
            try:
                pid = int(parts[1])
            except ValueError:
                continue
            if pid == os.getpid() or _pid_alive(pid):
                continue
            lease_row.expires_at = 0.0
            expired += 1
    if expired:
        logger.info("work_lease: {} lease мёртвых pid просрочены", expired)
    return expired


async def is_held(project_id: int, unit_key: str) -> bool:
    """Есть ли живой lease на единицу (для реконсайлеров: «шаг живой»)."""
    from sqlalchemy import select

    async with session_scope() as session:
        row = (
            await session.execute(
                select(WorkLease.expires_at).where(
                    WorkLease.project_id == project_id,
                    WorkLease.unit_key == unit_key,
                )
            )
        ).scalar_one_or_none()
    return row is not None and float(row) > time.time()


@asynccontextmanager
async def lease_unit(
    project_id: int,
    unit_key: str,
    *,
    ttl_s: float | None = None,
) -> AsyncIterator[bool]:
    """Контекст: захват → работа → release в finally (только своего)."""
    me = current_owner()
    got = await acquire(project_id, unit_key, owner=me, ttl_s=ttl_s)
    try:
        yield got
    finally:
        if got:
            await release(project_id, unit_key, owner=me)
