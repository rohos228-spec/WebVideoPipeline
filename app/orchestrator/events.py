"""SSE-шина оркестратора.

Монотонный ``seq`` на сессию. Token-события живут только в кольце на 1000
событий и в БД не пишутся. Номера для них берутся из памяти пачками
(``SEQ_RESERVE``): в ``orch_event_seq`` уходит верхняя отметка резерва,
не каждый токен. Укрупнённые события пишутся в ``orch_events`` через
одно долгоживущее соединение на базу (файл SQLite в WAL или Postgres
по ``DATABASE_URL``). Компакция — отдельно.

``publish`` отдаёт запись в рабочий поток (``asyncio.to_thread``).
Цикл на время commit не останавливается: кольцо и подписчики обновляются
уже после возврата из потока. Соединения в ``_CONNS`` живут до конца
процесса — одно на базу. Оборванное соединение (рестарт Postgres)
выбрасывается из ``_CONNS``, следующая запись открывает новое.
"""

from __future__ import annotations

import asyncio
import json
import threading
from collections import deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import bindparam, text

from app.orchestrator.contracts import (
    EPHEMERAL_EVENT_TYPES,
    EVENT_RETENTION_DAYS,
    EVENT_TYPES,
    RETAINED_EVENT_TYPES,
    SESSION_EVENT_CAP,
    TOKEN_RING_SIZE,
    OrchEvent,
)
from app.orchestrator.db.migrations import connect, target_key

# Сколько seq резервировать одним UPDATE. Токен не трогает БД, пока пачка не кончилась.
SEQ_RESERVE = 64

__all__ = [
    "EVENT_TYPES",
    "EventBus",
    "forget_seq_batch",
    "persisted_types",
    "seq_reserve_count",
]

_LOCK = threading.RLock()
_CONNS: dict[str, Any] = {}
_BATCHES: dict[tuple[str, str], list[int]] = {}
_RESERVES: dict[tuple[str, str], int] = {}

_KEEP_SQL = ", ".join(f"'{name}'" for name in sorted(RETAINED_EVENT_TYPES))


class EventBus:
    def __init__(self) -> None:
        self._rings: dict[str, deque[OrchEvent]] = {}
        self._subs: dict[str, set[asyncio.Queue[OrchEvent]]] = {}
        # Один lock на сессию: insert и рассылка не обгоняют друг друга.
        self._order: dict[str, asyncio.Lock] = {}

    def _session_lock(self, session_id: str) -> asyncio.Lock:
        lock = self._order.get(session_id)
        if lock is None:
            lock = asyncio.Lock()
            self._order[session_id] = lock
        return lock

    async def publish(
        self,
        session_id: str,
        event_type: str,
        payload: dict[str, Any] | None = None,
        *,
        task_id: int | None = None,
        created_at: str | None = None,
    ) -> OrchEvent:
        """Запись и рассылка одной сессии идут строго по seq.

        ``asyncio.to_thread`` сам по себе может вернуть более поздний commit
        раньше. Курсор SSE хранит только максимум, поэтому меньший seq,
        пришедший позже, потерялся бы до переподключения.
        """
        async with self._session_lock(session_id):
            ephemeral = event_type in EPHEMERAL_EVENT_TYPES
            seq, _stored_at = await asyncio.to_thread(
                _insert_event,
                session_id,
                event_type,
                payload or {},
                task_id=task_id,
                created_at=created_at,
                persist=not ephemeral,
            )
            event = OrchEvent(
                type=event_type,
                task_id=task_id,
                payload=dict(payload or {}),
                seq=seq,
                session_id=session_id,
            )
            if ephemeral:
                ring = self._rings.setdefault(session_id, deque(maxlen=TOKEN_RING_SIZE))
                ring.append(event)
            for queue in list(self._subs.get(session_id, ())):
                queue.put_nowait(event)
            return event

    def replay(self, session_id: str, last_seq: int) -> list[OrchEvent]:
        """События с ``seq > last_seq``: БД (укрупнённые) + кольцо token."""
        return self._merge_replay(session_id, last_seq, _load_persisted(session_id, last_seq))

    async def replay_from(self, session_id: str, last_seq: int) -> list[OrchEvent]:
        """То же, что ``replay``, но чтение ``orch_events`` не занимает цикл.

        Тот же lock, что у ``publish``: replay не видит N+1, пока N не записан
        и не попал в живую очередь.
        """
        async with self._session_lock(session_id):
            persisted = await asyncio.to_thread(_load_persisted, session_id, last_seq)
            return self._merge_replay(session_id, last_seq, persisted)

    def _merge_replay(self, session_id: str, last_seq: int, events: list[OrchEvent]) -> list[OrchEvent]:
        for event in self._rings.get(session_id, ()):
            if event.seq > last_seq:
                events.append(event)
        events.sort(key=lambda item: item.seq)
        return events

    def subscribe(self, session_id: str) -> asyncio.Queue[OrchEvent]:
        queue: asyncio.Queue[OrchEvent] = asyncio.Queue()
        self._subs.setdefault(session_id, set()).add(queue)
        return queue

    def unsubscribe(self, session_id: str, queue: asyncio.Queue[OrchEvent]) -> None:
        subs = self._subs.get(session_id)
        if not subs:
            return
        subs.discard(queue)
        if not subs:
            self._subs.pop(session_id, None)

    @asynccontextmanager
    async def subscription(self, session_id: str) -> AsyncIterator[asyncio.Queue[OrchEvent]]:
        queue = self.subscribe(session_id)
        try:
            yield queue
        finally:
            self.unsubscribe(session_id, queue)


def compact_events(
    session_id: str | None = None,
    *,
    now: datetime | None = None,
    retention_days: int = EVENT_RETENTION_DAYS,
    cap: int = SESSION_EVENT_CAP,
) -> dict[str, int]:
    """Удалить события старше ``retention_days``, кроме checkpoint/approval.

    Затем жёсткий кап ``cap`` на сессию: сначала старые обычные события,
    если их не хватает — самые старые защищённые тоже.
    """
    moment = now or datetime.now(UTC)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    cutoff = (moment - timedelta(days=retention_days)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    conn = connect()
    try:
        if session_id is None:
            sessions = list(conn.execute(text("SELECT DISTINCT session_id FROM orch_events")).scalars())
        else:
            sessions = [session_id]
        deleted_age = 0
        deleted_cap = 0
        for sid in sessions:
            cur = conn.execute(
                text(
                    f"""
                    DELETE FROM orch_events
                    WHERE session_id = :sid
                      AND created_at < :cutoff
                      AND type NOT IN ({_KEEP_SQL})
                    """
                ),
                {"sid": sid, "cutoff": cutoff},
            )
            deleted_age += cur.rowcount if cur.rowcount is not None else 0
            deleted_cap += _enforce_cap(conn, sid, cap)
        conn.commit()
        return {"deleted_age": deleted_age, "deleted_cap": deleted_cap}
    finally:
        conn.close()


def _enforce_cap(conn: Any, session_id: str, cap: int) -> int:
    rows = (
        conn.execute(
            text("SELECT id, type FROM orch_events WHERE session_id = :sid ORDER BY seq ASC, id ASC"),
            {"sid": session_id},
        )
        .mappings()
        .fetchall()
    )
    overflow = len(rows) - cap
    if overflow <= 0:
        return 0
    ordinary = [row["id"] for row in rows if row["type"] not in RETAINED_EVENT_TYPES]
    victims = ordinary[:overflow]
    if not victims:
        return 0
    conn.execute(
        text("DELETE FROM orch_events WHERE id IN :ids").bindparams(bindparam("ids", expanding=True)),
        {"ids": victims},
    )
    return len(victims)


def seq_reserve_count(session_id: str, path: Path | None = None) -> int:
    """Сколько раз для сессии писали верхнюю отметку seq (не число событий)."""
    key = (target_key(path), session_id)
    with _LOCK:
        return _RESERVES.get(key, 0)


def forget_seq_batch(session_id: str, path: Path | None = None) -> None:
    """Сбросить память пачки, как после рестарта процесса. Отметка в БД остаётся."""
    key = (target_key(path), session_id)
    with _LOCK:
        _BATCHES.pop(key, None)


def _event_conn(key: str) -> Any:
    """Одно соединение на базу. Вызывать под ``_LOCK``.

    PRAGMA для SQLite (WAL, ``busy_timeout=5000``, внешние ключи) ставит
    движок ``app.orchestrator.db.engine`` при открытии соединения.
    """
    conn = _CONNS.get(key)
    if conn is not None:
        return conn
    conn = connect()
    _CONNS[key] = conn
    return conn


def _take_seq(db_key: str, session_id: str) -> int:
    """Следующий seq. БД обновляется пачкой ``SEQ_RESERVE``, не на каждый токен."""
    key = (db_key, session_id)
    with _LOCK:
        window = _BATCHES.get(key)
        if window is None or window[0] > window[1]:
            conn = _event_conn(db_key)
            window = _reserve_seq(conn, session_id)
            _BATCHES[key] = window
            _RESERVES[key] = _RESERVES.get(key, 0) + 1
        seq = window[0]
        window[0] = seq + 1
        return seq


def _reserve_seq(conn: Any, session_id: str) -> list[int]:
    try:
        stored = conn.execute(
            text("SELECT seq FROM orch_event_seq WHERE session_id = :sid"),
            {"sid": session_id},
        ).scalar()
        current = int(stored) if stored is not None else 0
        high = current + SEQ_RESERVE
        if stored is None:
            conn.execute(
                text("INSERT INTO orch_event_seq (session_id, seq) VALUES (:sid, :seq)"),
                {"sid": session_id, "seq": high},
            )
        else:
            conn.execute(
                text("UPDATE orch_event_seq SET seq = :seq WHERE session_id = :sid"),
                {"sid": session_id, "seq": high},
            )
        conn.commit()
    except BaseException:
        _rollback(conn)
        raise
    return [current + 1, high]


def _rollback(conn: Any) -> None:
    try:
        conn.rollback()
    except Exception:
        return


def _drop_if_broken(key: str) -> None:
    """Соединение, которое SQLAlchemy признал оборванным, больше не держим.

    Вызывать под ``_LOCK``. На SQLite обрывов не бывает; на Postgres это
    рестарт сервера или сети — без сброса шина писала бы в мёртвое
    соединение до рестарта процесса.
    """
    conn = _CONNS.get(key)
    if conn is None or not (getattr(conn, "invalidated", False) or getattr(conn, "closed", False)):
        return
    _CONNS.pop(key, None)
    try:
        conn.close()
    except Exception:
        return


def _insert_event(
    session_id: str,
    event_type: str,
    payload: dict[str, Any],
    *,
    task_id: int | None,
    created_at: str | None,
    persist: bool,
) -> tuple[int, str]:
    """Резерв seq, insert и commit под одним ``threading.RLock``.

    ``_CONNS`` открыты из пула потоков. Без этого лока два ``publish``
    делят одно соединение.
    """
    db_key = target_key()
    with _LOCK:
        try:
            conn = _event_conn(db_key)
            seq = _take_seq(db_key, session_id)
        except BaseException:
            _drop_if_broken(db_key)
            raise
        stamp = created_at or _utc_stamp(conn)
        if persist:
            try:
                conn.execute(
                    text(
                        """
                        INSERT INTO orch_events (session_id, task_id, seq, type, payload_json, created_at)
                        VALUES (:sid, :task_id, :seq, :type, :payload, :created_at)
                        """
                    ),
                    {
                        "sid": session_id,
                        "task_id": task_id,
                        "seq": seq,
                        "type": event_type,
                        "payload": json.dumps(payload, ensure_ascii=False, default=str),
                        "created_at": stamp,
                    },
                )
                conn.commit()
            except BaseException:
                _rollback(conn)
                _drop_if_broken(db_key)
                raise
        return seq, stamp


def _utc_stamp(conn: Any) -> str:
    del conn
    from app.orchestrator.db.migrations import utc_now

    return utc_now()


def _load_persisted(session_id: str, last_seq: int) -> list[OrchEvent]:
    conn = connect()
    try:
        rows = (
            conn.execute(
                text(
                    """
                    SELECT session_id, task_id, seq, type, payload_json
                    FROM orch_events
                    WHERE session_id = :sid AND seq > :last_seq
                    ORDER BY seq ASC
                    """
                ),
                {"sid": session_id, "last_seq": int(last_seq)},
            )
            .mappings()
            .fetchall()
        )
    finally:
        conn.close()
    events: list[OrchEvent] = []
    for row in rows:
        payload = json.loads(row["payload_json"] or "{}")
        events.append(
            OrchEvent(
                type=row["type"],
                task_id=row["task_id"],
                payload=payload if isinstance(payload, dict) else {"value": payload},
                seq=int(row["seq"]),
                session_id=row["session_id"],
            )
        )
    return events


def persisted_types(session_id: str) -> list[str]:
    conn = connect()
    try:
        rows: list[Any] = list(
            conn.execute(
                text("SELECT type FROM orch_events WHERE session_id = :sid ORDER BY seq ASC"),
                {"sid": session_id},
            ).scalars()
        )
        return [str(item) for item in rows]
    finally:
        conn.close()
