"""журнал публикаций: publications, publish_approvals, publication_manual_actions.

Revision ID: 0019
Revises: 0018
Create Date: 2026-10-22

Таблицы перенесены из движка (int/chat-v1, его миграции 0012 и 0017), где
создавались сырым DDL SQLite. Код, который их читает и пишет, —
`app/services/publications_store.py` (журнал намерения до клика, аренда
площадки, одноразовые разрешения, ручные действия оператора) и
`publish_accounts.recent_click_times` (дневной лимит публикаций).

Таблицы не в `app.models.Base`, поэтому baseline 0001 их не создаёт ни на
свежей базе, ни на базе заказчика — нужна отдельная ревизия. Создание при
отсутствии (идемпотентно, как 0014): `publications_store.ensure_schema` мог
успеть создать их раньше на работающей базе.

Колонки арендатора и политик RLS здесь сознательно нет: относить ли журнал
публикаций к данным арендатора, решает заказчик. Когда решит — отдельной
ревизией вместе с записью в `app/services/tenant_tables.py`.

Схема — 1:1 с `publications_store` на дату ревизии. Запрет UPDATE/DELETE на
`publication_manual_actions` — триггерами SQLite, как у остальных журналов
движка.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SQLITE_TRIGGERS = (
    "CREATE TRIGGER IF NOT EXISTS publication_manual_no_update BEFORE UPDATE ON publication_manual_actions "
    "BEGIN SELECT RAISE(ABORT, 'publication manual actions are append-only'); END",
    "CREATE TRIGGER IF NOT EXISTS publication_manual_no_delete BEFORE DELETE ON publication_manual_actions "
    "BEGIN SELECT RAISE(ABORT, 'publication manual actions are append-only'); END",
)


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())

    if "publications" not in tables:
        op.create_table(
            "publications",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("project_id", sa.Integer(), nullable=False),
            sa.Column("platform", sa.Text(), nullable=False),
            sa.Column("artifact_uuid", sa.Text(), nullable=True),
            sa.Column("sha256", sa.Text(), nullable=False),
            sa.Column("account_id", sa.Text(), nullable=True),
            sa.Column("status", sa.Text(), nullable=False),
            sa.Column("post_url", sa.Text(), nullable=True),
            sa.Column("post_id", sa.Text(), nullable=True),
            sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("screenshot", sa.Text(), nullable=True),
            sa.Column("error_code", sa.Text(), nullable=True),
            sa.Column("lease_owner", sa.Text(), nullable=True),
            sa.Column("lease_until", sa.Float(), nullable=True),
            sa.Column("clicked_at", sa.Float(), nullable=True),
            sa.Column("created_at", sa.Float(), nullable=False),
            sa.Column("updated_at", sa.Float(), nullable=False),
            sqlite_autoincrement=True,
        )
        op.create_index("ux_publications_idem", "publications", ["project_id", "platform", "sha256"],
                        unique=True)
        op.create_index("ix_publications_project", "publications", ["project_id"])

    if "publish_approvals" not in tables:
        op.create_table(
            "publish_approvals",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("project_id", sa.Integer(), nullable=False),
            sa.Column("platform", sa.Text(), nullable=False),
            sa.Column("sha256", sa.Text(), nullable=False),
            sa.Column("caption_sha256", sa.Text(), nullable=False),
            sa.Column("approved_by", sa.Text(), nullable=False),
            sa.Column("approved_at", sa.Float(), nullable=False),
            sa.Column("consumed_at", sa.Float(), nullable=True),
            sqlite_autoincrement=True,
        )
        op.create_index("ux_publish_approvals_key", "publish_approvals", ["project_id", "platform", "sha256"],
                        unique=True)

    if "publication_manual_actions" not in tables:
        op.create_table(
            "publication_manual_actions",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("publication_id", sa.Integer(), nullable=False),
            sa.Column("project_id", sa.Integer(), nullable=False),
            sa.Column("platform", sa.Text(), nullable=False),
            sa.Column("sha256", sa.Text(), nullable=False),
            sa.Column("action", sa.Text(), nullable=False),
            sa.Column("prev_status", sa.Text(), nullable=True),
            sa.Column("prev_post_url", sa.Text(), nullable=True),
            sa.Column("post_url", sa.Text(), nullable=True),
            sa.Column("reason", sa.Text(), nullable=True),
            sa.Column("actor", sa.Text(), nullable=False),
            sa.Column("created_at", sa.Float(), nullable=False),
            sqlite_autoincrement=True,
        )
        op.create_index("ix_publication_manual_project", "publication_manual_actions", ["project_id"])

    if bind.dialect.name == "sqlite":
        for ddl in _SQLITE_TRIGGERS:
            op.execute(ddl)


def downgrade() -> None:
    """Служебный откат для теста, который проходит базу заказчика сквозь все ревизии.

    Журнал публикаций при этом теряется — откат ниже 0018 на живой базе
    делается только из копии (`scripts/backup_db.py`).
    """
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "publication_manual_actions" in tables:
        if bind.dialect.name == "sqlite":
            op.execute("DROP TRIGGER IF EXISTS publication_manual_no_update")
            op.execute("DROP TRIGGER IF EXISTS publication_manual_no_delete")
        op.drop_index("ix_publication_manual_project", table_name="publication_manual_actions")
        op.drop_table("publication_manual_actions")
    if "publish_approvals" in tables:
        op.drop_index("ux_publish_approvals_key", table_name="publish_approvals")
        op.drop_table("publish_approvals")
    if "publications" in tables:
        op.drop_index("ix_publications_project", table_name="publications")
        op.drop_index("ux_publications_idem", table_name="publications")
        op.drop_table("publications")
