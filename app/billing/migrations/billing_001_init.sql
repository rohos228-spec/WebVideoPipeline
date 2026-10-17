CREATE TABLE IF NOT EXISTS wallets (
    user_id TEXT PRIMARY KEY,
    balance_credits INTEGER NOT NULL DEFAULT 0 CHECK (balance_credits >= 0),
    updated_at TEXT NOT NULL
);
-- Журнал: только дописывается. Каждая операция = txn + проводки (сумма проводок = 0).
CREATE TABLE IF NOT EXISTS ledger_txns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    idempotency_key TEXT NOT NULL UNIQUE,
    user_id TEXT NOT NULL,
    type TEXT NOT NULL,          -- topup | charge | refund | settle | adjust
    purpose TEXT,                -- topup | package | subscription | generation
    amount INTEGER NOT NULL,     -- изменение баланса пользователя (кредиты)
    ref TEXT,
    meta TEXT,
    expires_at TEXT,             -- всегда NULL: кредиты не сгорают
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_ledger_user ON ledger_txns(user_id, id);
CREATE TABLE IF NOT EXISTS ledger_postings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    txn_id INTEGER NOT NULL REFERENCES ledger_txns(id),
    account TEXT NOT NULL,
    amount INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_postings_account ON ledger_postings(account);
CREATE TRIGGER IF NOT EXISTS ledger_txns_no_update BEFORE UPDATE ON ledger_txns
BEGIN SELECT RAISE(ABORT, 'ledger is append-only'); END;
CREATE TRIGGER IF NOT EXISTS ledger_txns_no_delete BEFORE DELETE ON ledger_txns
BEGIN SELECT RAISE(ABORT, 'ledger is append-only'); END;
CREATE TRIGGER IF NOT EXISTS ledger_postings_no_update BEFORE UPDATE ON ledger_postings
BEGIN SELECT RAISE(ABORT, 'ledger is append-only'); END;
CREATE TRIGGER IF NOT EXISTS ledger_postings_no_delete BEFORE DELETE ON ledger_postings
BEGIN SELECT RAISE(ABORT, 'ledger is append-only'); END;
CREATE TABLE IF NOT EXISTS payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider_id TEXT NOT NULL UNIQUE,
    user_id TEXT NOT NULL,
    status TEXT NOT NULL,
    amount_rub TEXT NOT NULL,
    purpose TEXT NOT NULL,
    plan TEXT,
    period TEXT,
    test INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS subscriptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    plan TEXT NOT NULL,
    period TEXT NOT NULL,
    started_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    payment_id TEXT
);
CREATE INDEX IF NOT EXISTS ix_sub_user ON subscriptions(user_id, id);
CREATE TABLE IF NOT EXISTS reservations (
    job_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    amount INTEGER NOT NULL,
    status TEXT NOT NULL,        -- held | settled | refunded
    operation TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
