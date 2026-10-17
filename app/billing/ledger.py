"""Кошелёк и журнал (двойная запись, идемпотентно, append-only)."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

from app.billing.db import connect, now_iso, tx


class InsufficientCredits(Exception):
    def __init__(self, need: int, have: int):
        super().__init__(f"Недостаточно кредитов: нужно {need}, доступно {have}")
        self.need, self.have = need, have


@dataclass
class Entry:
    id: int
    created: bool  # False — операция с этим ключом уже была (повтор)


def wallet_account(user_id: str) -> str:
    return f"wallet:{user_id}"


def balance(user_id: str) -> int:
    with connect() as conn:
        row = conn.execute("SELECT balance_credits FROM wallets WHERE user_id=?", (str(user_id),)).fetchone()
        return int(row[0]) if row else 0


def post(
    conn: sqlite3.Connection,
    *,
    user_id: str,
    type: str,
    amount: int,
    counter_account: str,
    idempotency_key: str,
    purpose: str | None = None,
    ref: str | None = None,
    meta: dict | None = None,
) -> Entry:
    """Проводка внутри открытой транзакции. amount — изменение баланса пользователя.

    Двойная запись: wallet:<user> (+amount) и counter_account (−amount).
    Повтор с тем же idempotency_key ничего не меняет.
    """
    uid = str(user_id)
    amount = int(amount)
    got = conn.execute("SELECT id FROM ledger_txns WHERE idempotency_key=?", (idempotency_key,)).fetchone()
    if got:
        return Entry(int(got[0]), False)
    ts = now_iso()
    conn.execute(
        "INSERT INTO wallets(user_id, balance_credits, updated_at) VALUES (?, 0, ?) ON CONFLICT(user_id) DO NOTHING",
        (uid, ts),
    )
    have = int(conn.execute("SELECT balance_credits FROM wallets WHERE user_id=?", (uid,)).fetchone()[0])
    if have + amount < 0:
        raise InsufficientCredits(-amount, have)
    cur = conn.execute(
        "INSERT INTO ledger_txns(idempotency_key, user_id, type, purpose, amount, ref, meta, expires_at, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?)",
        (idempotency_key, uid, type, purpose, amount, ref, json.dumps(meta or {}, ensure_ascii=False), ts),
    )
    txn_id = int(cur.lastrowid)
    if amount:
        conn.executemany(
            "INSERT INTO ledger_postings(txn_id, account, amount) VALUES (?, ?, ?)",
            [(txn_id, wallet_account(uid), amount), (txn_id, counter_account, -amount)],
        )
    conn.execute("UPDATE wallets SET balance_credits=?, updated_at=? WHERE user_id=?", (have + amount, ts, uid))
    return Entry(txn_id, True)


def credit(user_id: str, amount: int, *, idempotency_key: str, purpose: str = "topup",
           ref: str | None = None, meta: dict | None = None) -> Entry:
    with tx() as conn:
        return post(conn, user_id=user_id, type="topup", amount=amount, counter_account="system:payments",
                    idempotency_key=idempotency_key, purpose=purpose, ref=ref, meta=meta)


def entries(user_id: str, limit: int = 100) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, user_id, type, purpose, amount, ref, meta, expires_at, created_at FROM ledger_txns"
            " WHERE user_id=? AND type != 'settle' ORDER BY id DESC LIMIT ?",
            (str(user_id), int(limit)),
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["meta"] = json.loads(d["meta"] or "{}")
        out.append(d)
    return out


def check_integrity() -> bool:
    """Сумма всех проводок = 0, баланс кошельков = сумма их проводок."""
    with connect() as conn:
        total = conn.execute("SELECT COALESCE(SUM(amount),0) FROM ledger_postings").fetchone()[0]
        bad = conn.execute(
            "SELECT COUNT(*) FROM wallets w WHERE w.balance_credits != (SELECT COALESCE(SUM(amount),0)"
            " FROM ledger_postings p WHERE p.account = 'wallet:' || w.user_id)"
        ).fetchone()[0]
    return total == 0 and bad == 0
