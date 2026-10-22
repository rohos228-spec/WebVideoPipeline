-- Арендатор у таблиц чата оркестратора (alembic 0020). Чат открыт каждому
-- клиенту студии: задачи, шаги, события и сообщения — данные арендатора.
-- На SQLite политик RLS нет (многоарендный режим там запрещён), колонка
-- нужна, чтобы схема совпадала с Postgres. NULL — строки владельца.

ALTER TABLE orch_reports ADD COLUMN tenant_id CHAR(32);
ALTER TABLE orch_report_versions ADD COLUMN tenant_id CHAR(32);
ALTER TABLE orch_tasks ADD COLUMN tenant_id CHAR(32);
ALTER TABLE orch_steps ADD COLUMN tenant_id CHAR(32);
ALTER TABLE orch_checkpoints ADD COLUMN tenant_id CHAR(32);
ALTER TABLE orch_events ADD COLUMN tenant_id CHAR(32);
ALTER TABLE orch_event_seq ADD COLUMN tenant_id CHAR(32);
ALTER TABLE orch_messages ADD COLUMN tenant_id CHAR(32);
ALTER TABLE orch_llm_calls ADD COLUMN tenant_id CHAR(32);

CREATE INDEX IF NOT EXISTS ix_orch_reports_tenant_id ON orch_reports (tenant_id);
CREATE INDEX IF NOT EXISTS ix_orch_report_versions_tenant_id ON orch_report_versions (tenant_id);
CREATE INDEX IF NOT EXISTS ix_orch_tasks_tenant_id ON orch_tasks (tenant_id);
CREATE INDEX IF NOT EXISTS ix_orch_steps_tenant_id ON orch_steps (tenant_id);
CREATE INDEX IF NOT EXISTS ix_orch_checkpoints_tenant_id ON orch_checkpoints (tenant_id);
CREATE INDEX IF NOT EXISTS ix_orch_events_tenant_id ON orch_events (tenant_id);
CREATE INDEX IF NOT EXISTS ix_orch_event_seq_tenant_id ON orch_event_seq (tenant_id);
CREATE INDEX IF NOT EXISTS ix_orch_messages_tenant_id ON orch_messages (tenant_id);
CREATE INDEX IF NOT EXISTS ix_orch_llm_calls_tenant_id ON orch_llm_calls (tenant_id);
