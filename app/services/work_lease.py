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
from typing import Any

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


def _node_identity() -> tuple[str, bool]:
    """(имя узла, объявлено ли оно явно).

    По умолчанию — `socket.gethostname()`, и в контейнере это его id, который
    **меняется при каждой пересборке**. Owner'ы прошлого контейнера после
    выкладки выглядят как чужой узел: `expire_dead_local_leases` их пропускает
    (`parts[0] != _HOST`), `_reclaim_local_orphan` тоже. Значит любая выкладка
    поверх идущего шага морозила проект на весь TTL — час, — и снаружи это
    неотличимо от «ничего не происходит».

    `FLEET_NODE_NAME` даёт узлу имя, переживающее пересборку. Второй элемент
    кортежа говорит, объявлено ли оно человеком: от этого зависит право
    считать узел однопроцессным (см. `expire_dead_local_leases`).
    """
    try:
        from app.settings import settings

        name = (settings.fleet_node_name or "").strip()
    except Exception:  # noqa: BLE001 — на голых тестовых стендах settings может не подняться
        name = ""
    return (name, True) if name else (socket.gethostname(), False)


_HOST, _HOST_IS_DECLARED = _node_identity()

# owner текущей asyncio-задачи (uuid на задачу, лениво).
_task_owner: dict[int, str] = {}

# owner'ы, которые ЭТОТ процесс реально держит прямо сейчас. Нужны, чтобы
# отличить живой lease от осиротевшего: owner привязан к asyncio-задаче, и
# после её смерти (например, release не смог записать в БД) строка висит до
# конца TTL — час для шага, — а `expire_dead_local_leases` свой pid не трогает.
_live_owners: set[str] = set()

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
    params = {"pid": project_id, "key": unit_key, "me": me, "exp": expires, "now": now}
    rowcount = await _execute_with_retry(stmt, params)
    ok = rowcount > 0
    if not ok and await _reclaim_local_orphan(project_id, unit_key):
        rowcount = await _execute_with_retry(stmt, {**params, "now": time.time()})
        ok = rowcount > 0
    if ok:
        _live_owners.add(me)
    else:
        logger.debug("work_lease: {}/{} занят живым lease — пропуск", project_id, unit_key)
    return ok


async def _reclaim_local_orphan(project_id: int, unit_key: str) -> bool:
    """Просрочить lease, который держит ЭТОТ процесс, но уже никакая задача.

    Owner привязан к asyncio-задаче, и каждый такт воркера — новая задача.
    Значит свой же осиротевший lease (release не доехал до БД) процесс
    перехватить не может: `expire_dead_local_leases` пропускает собственный
    pid, а TTL шага — час. Живые owner'ы этого процесса лежат в
    `_live_owners`, поэтому осиротевший отличается от рабочего.
    """
    from sqlalchemy import select

    async with session_scope() as session:
        row = (
            await session.execute(
                select(WorkLease).where(
                    WorkLease.project_id == project_id,
                    WorkLease.unit_key == unit_key,
                )
            )
        ).scalar_one_or_none()
        if row is None or float(row.expires_at) <= time.time():
            return False
        parts = (row.owner or "").split(":")
        if len(parts) < 3 or parts[0] != _HOST:
            return False
        try:
            pid = int(parts[1])
        except ValueError:
            return False
        if pid != os.getpid() or row.owner in _live_owners:
            return False
        row.expires_at = 0.0
        logger.warning(
            "work_lease: {}/{} — осиротевший lease своего процесса (owner={}), перехватываю",
            project_id,
            unit_key,
            row.owner,
        )
        return True


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


async def release(
    project_id: int,
    unit_key: str,
    *,
    owner: str | None = None,
    session: Any | None = None,
) -> bool:
    """Освобождение ТОЛЬКО своего lease (чужой не трогаем — fencing).

    `session` — сессия вызывающего. Её надо передавать всегда, когда она уже
    открыта и что-то писала: SQLite пускает одного writer'а на файл, и своя
    короткая сессия встанет на busy_timeout (60 с × ретраи) в ожидании
    транзакции, которую держит сам вызывающий. Ровно этот самозахват
    подвешивал `advance_project` на три минуты, а потом оставлял lease
    висеть на весь TTL.
    """
    me = owner or current_owner()
    stmt = delete(WorkLease).where(
        WorkLease.project_id == project_id,
        WorkLease.unit_key == unit_key,
        WorkLease.owner == me,
    )
    try:
        if session is not None:
            res = await session.execute(stmt)
            return int(res.rowcount or 0) > 0
        return (await _execute_with_retry(stmt)) > 0
    except Exception as e:  # noqa: BLE001 — release не должен ронять шаг
        logger.warning("work_lease: release {}/{}: {}", project_id, unit_key, e)
        return False
    finally:
        _live_owners.discard(me)


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True  # PermissionError и т.п. — процесс есть
    return True


async def expire_dead_local_leases() -> int:
    """Lease'ы прошлых поколений ЭТОГО узла → просроченные.

    Зовётся на старте (`startup_guard`): перехват осиротевших единиц без
    ожидания полного TTL, который для шага равен часу.
    """
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
            if pid == os.getpid():
                continue
            if _pid_alive(pid) and not _HOST_IS_DECLARED:
                continue
            # `_pid_alive` смотрит в СВОЁ пространство pid. У пересобранного
            # контейнера оно новое: приложение прошлого поколения было pid 6, и
            # pid 6 у нового поколения тоже занят — своим же процессом. Проверка
            # уверенно отвечает «жив» про давно убитый процесс, и lease висит
            # весь TTL.
            #
            # Спасает только объявленное имя узла. Оно означает «здесь один
            # экземпляр приложения», а мы сейчас на старте — то есть этот
            # экземпляр и есть мы. Любой lease под нашим именем с чужим pid
            # остался от прошлого поколения, чем бы оно ни кончилось.
            #
            # Без объявленного имени правило неверно: на машине разработчика два
            # запущенных экземпляра делят hostname, и мы отобрали бы живую
            # работу у соседа. Там остаётся прежнее поведение — только мёртвые
            # pid.
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
