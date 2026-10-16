"""Хук списаний для очереди генераций (интегрирует агент A).

API:
    reserve(user_id, job_id, *, model, kind, units=1) -> int   # списывает (hold), 402 при нехватке
    settle(job_id)                                            # успех: hold → выручка
    refund(job_id, reason="")                                 # ошибка: автовозврат кредитами
    with charged(user_id, job_id, model=..., kind=...):        # авто: успех → settle, исключение → refund
        ...

Все функции идемпотентны по job_id.
"""

from __future__ import annotations

from contextlib import contextmanager

from app.billing import ledger, prices
from app.billing.db import now_iso, tx
from app.billing.ledger import InsufficientCredits

__all__ = ["InsufficientCredits", "reserve", "settle", "refund", "charged", "reservation"]


def reserve(user_id: str, job_id: str, *, model: str | None = None, kind: str | None = None,
            units: int = 1, amount: int | None = None) -> int:
    cost = int(amount) if amount is not None else prices.operation_price(model, kind, units)
    with tx() as conn:
        row = conn.execute("SELECT amount FROM reservations WHERE job_id=?", (job_id,)).fetchone()
        if row:
            return int(row[0])
        ledger.post(conn, user_id=user_id, type="charge", amount=-cost, counter_account=f"hold:{job_id}",
                    idempotency_key=f"charge:{job_id}", purpose="generation", ref=job_id,
                    meta={"model": model, "kind": kind, "units": units})
        ts = now_iso()
        conn.execute(
            "INSERT INTO reservations(job_id, user_id, amount, status, operation, created_at, updated_at)"
            " VALUES (?, ?, ?, 'held', ?, ?, ?)",
            (job_id, str(user_id), cost, f"{kind or ''}:{model or ''}", ts, ts),
        )
    return cost


def _finish(job_id: str, status: str, reason: str = "") -> bool:
    with tx() as conn:
        row = conn.execute("SELECT user_id, amount, status FROM reservations WHERE job_id=?", (job_id,)).fetchone()
        if not row or row["status"] != "held":
            return False
        uid, amount = row["user_id"], int(row["amount"])
        if status == "refunded":
            ledger.post(conn, user_id=uid, type="refund", amount=amount, counter_account=f"hold:{job_id}",
                        idempotency_key=f"refund:{job_id}", purpose="generation", ref=job_id,
                        meta={"reason": reason[:300]})
        else:
            # Пользовательский баланс не меняется: hold → выручка (служебная проводка).
            conn.execute(
                "INSERT INTO ledger_txns(idempotency_key, user_id, type, purpose, amount, ref, meta, created_at)"
                " VALUES (?, ?, 'settle', 'generation', 0, ?, '{}', ?)",
                (f"settle:{job_id}", uid, job_id, now_iso()),
            )
            tid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            conn.executemany(
                "INSERT INTO ledger_postings(txn_id, account, amount) VALUES (?, ?, ?)",
                [(tid, f"hold:{job_id}", amount), (tid, "system:revenue", -amount)],
            )
        conn.execute("UPDATE reservations SET status=?, updated_at=? WHERE job_id=?", (status, now_iso(), job_id))
    return True


def settle(job_id: str) -> bool:
    return _finish(job_id, "settled")


def refund(job_id: str, reason: str = "") -> bool:
    return _finish(job_id, "refunded", reason)


def reservation(job_id: str) -> dict | None:
    from app.billing.db import connect

    with connect() as conn:
        row = conn.execute("SELECT * FROM reservations WHERE job_id=?", (job_id,)).fetchone()
        return dict(row) if row else None


@contextmanager
def charged(user_id: str, job_id: str, **kw):
    cost = reserve(user_id, job_id, **kw)
    try:
        yield cost
    except BaseException as e:
        refund(job_id, reason=type(e).__name__)
        raise
    else:
        settle(job_id)
