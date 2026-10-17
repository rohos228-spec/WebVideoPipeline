"""Жёсткие лимиты потоков по тарифу и роли. Сверх лимита — задача ждёт в очереди (не отклоняется).

API для очередей (интегрирует агент A):
    limit_for(user_id, kind, role=None, team_owner_id=None) -> int
    try_acquire(user_id, kind, job_id) -> "running" | "queued"
    release(user_id, kind, job_id) -> job_id, переведённый из queued в running, или None
    async with slot(user_id, kind, job_id): ...   # ждёт свободный поток
    state(user_id, kind) -> {"running", "waiting", "limit"}
kind: "llm" | "agents" | "images" | "video".
"""

from __future__ import annotations

import asyncio
import threading
from collections import deque
from contextlib import asynccontextmanager, suppress

from app.billing import subscriptions

KINDS = ("llm", "agents", "images", "video")
# Роли (агент D): owner/admin/user — по своему тарифу; member — по тарифу владельца команды.
TEAM_ROLES = ("member",)

_lock = threading.RLock()
_running: dict[tuple[str, str], set[str]] = {}
_waiting: dict[tuple[str, str], deque[str]] = {}
_events: dict[str, asyncio.Event] = {}


def _norm(kind: str) -> str:
    k = (kind or "").lower()
    return {"image": "images", "videos": "video", "agent": "agents", "text": "llm"}.get(k, k)


def limit_for(user_id: str, kind: str, role: str | None = None, team_owner_id: str | None = None) -> int:
    kind = _norm(kind)
    if kind not in KINDS:
        raise ValueError(f"unknown stream kind: {kind}")
    payer = team_owner_id if (role in TEAM_ROLES and team_owner_id) else user_id
    _plan, streams = subscriptions.streams_for(str(payer))
    return int(getattr(streams, kind))


def try_acquire(user_id: str, kind: str, job_id: str, *, limit: int | None = None) -> str:
    key = (str(user_id), _norm(kind))
    lim = limit if limit is not None else limit_for(user_id, kind)
    with _lock:
        run = _running.setdefault(key, set())
        wait = _waiting.setdefault(key, deque())
        if job_id in run:
            return "running"
        if job_id in wait:
            return "queued"
        if len(run) < lim and not wait:
            run.add(job_id)
            return "running"
        wait.append(job_id)
        return "queued"


def release(user_id: str, kind: str, job_id: str, *, limit: int | None = None) -> str | None:
    key = (str(user_id), _norm(kind))
    lim = limit if limit is not None else limit_for(user_id, kind)
    with _lock:
        run = _running.setdefault(key, set())
        wait = _waiting.setdefault(key, deque())
        run.discard(job_id)
        with suppress(ValueError):
            wait.remove(job_id)
        promoted = None
        if wait and len(run) < lim:
            promoted = wait.popleft()
            run.add(promoted)
            ev = _events.get(promoted)
            if ev is not None:
                ev.set()
        return promoted


def state(user_id: str, kind: str) -> dict:
    key = (str(user_id), _norm(kind))
    with _lock:
        return {
            "running": len(_running.get(key, ())),
            "waiting": len(_waiting.get(key, ())),
            "limit": limit_for(user_id, kind),
        }


@asynccontextmanager
async def slot(user_id: str, kind: str, job_id: str):
    ev = asyncio.Event()
    with _lock:
        _events[job_id] = ev
        st = try_acquire(user_id, kind, job_id)
    try:
        if st == "queued":
            await ev.wait()
        yield
    finally:
        with _lock:
            _events.pop(job_id, None)
        release(user_id, kind, job_id)


def reset() -> None:
    """Только для тестов."""
    with _lock:
        _running.clear()
        _waiting.clear()
        _events.clear()
