"""арендатор у таблиц движка оркестратора: колонка, индекс, RLS

Revision ID: 0020
Revises: 0019
Create Date: 2026-10-22

Чат оркестратора открыт каждому клиенту студии (роль `member`), а не только
админу. Значит история чата, планы и шаги, одобрения, журнал действий,
снимки для отката, предложения изменений, журнал наблюдения и медиа-задачи —
данные арендатора, и закрывать их должна та же политика, что проекты
(`docs/SAAS-PIVOT.md` §4.2). Классификация и причины, по которым касса
провайдеров и аварийный стоп остаются общими, —
`app/services/tenant_tables.py`.

Что делает:

* **Создаёт таблицы движка с моделями, если их нет.** До этой ревизии их
  заводил `create_all` на старте приложения (`_ensure_engine_schema`,
  `migrate_observation_schema`) — уже ПОСЛЕ миграций. На Postgres таблица,
  родившаяся после миграций, родилась бы без политики, и проверка
  `assert_rls_or_die` уронила бы старт. Теперь схема у них из миграции.
* **Колонка `tenant_id`** — тот же тип и та же обнуляемость, что в 0005:
  нативный UUID в Postgres, CHAR(32) в SQLite; строки до перехода
  принадлежат владельцу и остаются с NULL. Плюс индекс `ix_<таблица>_tenant_id`.
* **Postgres: значение по умолчанию** `nullif(current_setting('app.tenant_id',
  true), '')::uuid`. Сырой SQL движка (`orch_tasks`, `orch_events`, …) и
  Core-вставки в обход `before_flush` колонку не перечисляют — и получают
  арендатора транзакции, ту же настройку, по которой политика проверяет
  `WITH CHECK`. Забыть проставить нельзя, подставить чужого — тоже.
* **Postgres: ENABLE + FORCE + политика `tenant_isolation`** с предикатом
  0006 — слово в слово, чтобы `rls_check` и человек видели одну политику.

SQLite: колонка и индекс есть, политик нет — как у остальных таблиц
арендатора; многоарендный режим на SQLite запрещён `require_isolation`.

downgrade: снимает политики и значения по умолчанию, у сырых таблиц движка
убирает колонку. У таблиц с моделями колонка остаётся — её объявляет
модель, и baseline на свежей базе создаёт её так же.
"""

from __future__ import annotations

import importlib
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.services.tenant_tables import ENGINE_TENANT_TABLES, ORCH_RAW_TENANT_TABLES, policy_for

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TENANT = sa.Uuid(as_uuid=False)

#: Модули с моделями таблиц движка. Импорт регистрирует их в `Base.metadata`.
ENGINE_MODEL_MODULES: tuple[str, ...] = (
    "app.services.action_layer.store",
    "app.services.action_layer.attachments",
    "app.services.actions_recovery",
    "app.services.actions_v1",
    "app.services.change_control.store",
    "app.services.decision_config",
    "app.services.decision_state",
    "app.services.diagnosis",
    "app.services.managed_launch",
    "app.services.media_jobs",
    "app.services.observation",
    "app.services.outsee_idempotency",
    "app.services.profiles_state",
)

#: Предикат 0006, слово в слово.
_PREDICATE = """(
    CASE
        WHEN nullif(current_setting('app.tenant_id', true), '') IS NULL
            THEN tenant_id IS NULL
        ELSE tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid
    END
)"""

#: Арендатор транзакции как значение по умолчанию (только Postgres).
_DEFAULT = "nullif(current_setting('app.tenant_id', true), '')::uuid"


def _index_name(table: str) -> str:
    return f"ix_{table}_tenant_id"


def _engine_model_tables() -> list[sa.Table]:
    from app.models import Base

    for name in ENGINE_MODEL_MODULES:
        importlib.import_module(name)
    return [Base.metadata.tables[name] for name in ENGINE_TENANT_TABLES]


#: Файл раннера SQL движка с той же колонкой — SQLite-базы, которые раннер
#: строит без alembic (инструменты, тесты), получают ту же схему.
RUNNER_FILE = "010_orch_tenant.sql"


def _runner_tenant_sqlite(bind: sa.engine.Connection) -> None:
    """SQLite: файл раннера и запись в его журнал — схема байт в байт как у раннера."""
    from app.orchestrator.db.migrations import JOURNAL_DDL, sqlite_statements, utc_now

    if not sa.inspect(bind).has_table("orch_tasks"):
        return
    bind.exec_driver_sql(JOURNAL_DDL)
    applied = {row[0] for row in bind.exec_driver_sql("SELECT name FROM orch_schema_migrations")}
    if RUNNER_FILE in applied:
        return

    def has_column(table: str, column: str) -> bool:
        inspector = sa.inspect(bind)
        if not inspector.has_table(table):
            return False
        return any(col["name"] == column for col in inspector.get_columns(table))

    for statement in sqlite_statements(RUNNER_FILE, has_column):
        bind.exec_driver_sql(statement)
    bind.execute(
        sa.text("INSERT INTO orch_schema_migrations (name, applied_at) VALUES (:name, :at)"),
        {"name": RUNNER_FILE, "at": utc_now()},
    )


def upgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"
    if not is_pg:
        _runner_tenant_sqlite(bind)

    model_tables = _engine_model_tables()
    from app.models import Base

    Base.metadata.create_all(bind, tables=model_tables, checkfirst=True)

    inspector = sa.inspect(bind)
    existing = set(inspector.get_table_names())
    for table in ENGINE_TENANT_TABLES + ORCH_RAW_TENANT_TABLES:
        if table not in existing:
            continue
        columns = {c["name"] for c in inspector.get_columns(table)}
        if "tenant_id" not in columns:
            op.add_column(table, sa.Column("tenant_id", _TENANT, nullable=True))
        indexed = {tuple(ix.get("column_names") or ()) for ix in inspector.get_indexes(table)}
        if ("tenant_id",) not in indexed:
            op.create_index(_index_name(table), table, ["tenant_id"])
        if not is_pg:
            continue
        policy = policy_for(table)
        op.execute(f"ALTER TABLE {table} ALTER COLUMN tenant_id SET DEFAULT {_DEFAULT}")
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS {policy} ON {table}")
        op.execute(f"CREATE POLICY {policy} ON {table} USING {_PREDICATE} WITH CHECK {_PREDICATE}")


def downgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"
    inspector = sa.inspect(bind)
    existing = set(inspector.get_table_names())
    for table in ENGINE_TENANT_TABLES + ORCH_RAW_TENANT_TABLES:
        if table not in existing:
            continue
        columns = {c["name"] for c in inspector.get_columns(table)}
        if "tenant_id" not in columns:
            continue
        if is_pg:
            op.execute(f"DROP POLICY IF EXISTS {policy_for(table)} ON {table}")
            op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
            op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
            op.execute(f"ALTER TABLE {table} ALTER COLUMN tenant_id DROP DEFAULT")
        if table not in ORCH_RAW_TENANT_TABLES:
            continue
        names = {ix["name"] for ix in inspector.get_indexes(table)}
        if _index_name(table) in names:
            op.drop_index(_index_name(table), table_name=table)
        op.drop_column(table, "tenant_id")
    if not is_pg and "orch_schema_migrations" in existing:
        bind.execute(sa.text("DELETE FROM orch_schema_migrations WHERE name = :name"), {"name": RUNNER_FILE})
