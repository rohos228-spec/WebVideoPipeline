-- Настоящий провайдер в orch_llm_calls (alembic 0021). CHECK из 008 знал только 52pay и kie,
-- и учёт переписывал vibecode в «52pay»: журнал вызовов врал, через кого шёл запрос.
-- SQLite не меняет CHECK на месте: таблица пересобирается, строки и индексы те же.
-- SAVEPOINT: пересборка целиком или никак (и внутри транзакции alembic, и в раннере).

SAVEPOINT orch_llm_calls_provider;

CREATE TABLE orch_llm_calls_new (
    id INTEGER PRIMARY KEY,
    call_id TEXT NOT NULL UNIQUE,
    task_id TEXT,
    run_id TEXT,
    agent_role TEXT NOT NULL CHECK (
        agent_role IN ('supervisor', 'planner', 'critic', 'executor', 'classifier', 'summarizer')
    ),
    requested_tier TEXT NOT NULL CHECK (requested_tier IN ('fast', 'strong', 'auto')),
    tier TEXT NOT NULL CHECK (tier IN ('fast', 'strong', 'auto')),
    model TEXT NOT NULL,
    provider TEXT NOT NULL CHECK (provider IN ('52pay', 'kie', 'vibecode', 'unknown')),
    reasoning_effort TEXT,
    fallback_index INTEGER NOT NULL DEFAULT 0,
    attempt INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL CHECK (status IN ('ok', 'timeout', 'error', 'fixture_miss')),
    error TEXT,
    deterministic_flag INTEGER NOT NULL CHECK (deterministic_flag IN (0, 1)),
    classifier_confidence REAL,
    latency_ms INTEGER NOT NULL,
    prompt_tokens INTEGER NOT NULL,
    completion_tokens INTEGER NOT NULL,
    reasoning_tokens INTEGER NOT NULL,
    cost_usd REAL NOT NULL,
    budget_scope TEXT NOT NULL CHECK (budget_scope IN ('internal', 'pipeline')),
    router_impl TEXT NOT NULL CHECK (router_impl IN ('live', 'replay', 'scripted')),
    messages_sha256 TEXT,
    fixture_key TEXT,
    created_at TEXT NOT NULL,
    tenant_id CHAR(32)
);

INSERT INTO orch_llm_calls_new (
    id, call_id, task_id, run_id, agent_role, requested_tier, tier, model, provider,
    reasoning_effort, fallback_index, attempt, status, error, deterministic_flag,
    classifier_confidence, latency_ms, prompt_tokens, completion_tokens, reasoning_tokens,
    cost_usd, budget_scope, router_impl, messages_sha256, fixture_key, created_at, tenant_id
)
SELECT
    id, call_id, task_id, run_id, agent_role, requested_tier, tier, model, provider,
    reasoning_effort, fallback_index, attempt, status, error, deterministic_flag,
    classifier_confidence, latency_ms, prompt_tokens, completion_tokens, reasoning_tokens,
    cost_usd, budget_scope, router_impl, messages_sha256, fixture_key, created_at, tenant_id
FROM orch_llm_calls;

DROP TABLE orch_llm_calls;

ALTER TABLE orch_llm_calls_new RENAME TO orch_llm_calls;

CREATE INDEX IF NOT EXISTS ix_orch_llm_calls_task_created
    ON orch_llm_calls (task_id, created_at);

CREATE INDEX IF NOT EXISTS ix_orch_llm_calls_budget_scope
    ON orch_llm_calls (budget_scope);

CREATE INDEX IF NOT EXISTS ix_orch_llm_calls_tenant_id ON orch_llm_calls (tenant_id);

RELEASE orch_llm_calls_provider;
