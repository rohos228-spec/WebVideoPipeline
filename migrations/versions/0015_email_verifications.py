"""email_verifications table and studio_users email_verified / vk_user_id columns.

Revision ID: 0015
Revises: 0014
Create Date: 2026-09-28
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE_VERIFICATIONS = "email_verifications"
TABLE_USERS = "studio_users"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if TABLE_VERIFICATIONS not in tables:
        op.create_table(
            TABLE_VERIFICATIONS,
            sa.Column("id", sa.Uuid(as_uuid=False), primary_key=True),
            sa.Column("email", sa.String(length=200), nullable=False),
            sa.Column("code_hash", sa.String(length=128), nullable=False),
            sa.Column("purpose", sa.String(length=32), nullable=False, server_default="register"),
            sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("expires_at", sa.DateTime(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )
        op.create_index(f"ix_{TABLE_VERIFICATIONS}_email", TABLE_VERIFICATIONS, ["email"])
        op.create_index(f"ix_{TABLE_VERIFICATIONS}_expires_at", TABLE_VERIFICATIONS, ["expires_at"])

    if TABLE_USERS in tables:
        columns = {col["name"] for col in inspector.get_columns(TABLE_USERS)}
        if "email_verified" not in columns:
            op.add_column(
                TABLE_USERS,
                sa.Column("email_verified", sa.Boolean(), nullable=False, server_default=sa.true()),
            )
        if "vk_user_id" not in columns:
            op.add_column(
                TABLE_USERS,
                sa.Column("vk_user_id", sa.String(length=64), nullable=True),
            )
            op.create_index(f"ix_{TABLE_USERS}_vk_user_id", TABLE_USERS, ["vk_user_id"], unique=True)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if TABLE_VERIFICATIONS in tables:
        op.drop_table(TABLE_VERIFICATIONS)

    if TABLE_USERS in tables:
        columns = {col["name"] for col in inspector.get_columns(TABLE_USERS)}
        if "vk_user_id" in columns:
            op.drop_index(f"ix_{TABLE_USERS}_vk_user_id", table_name=TABLE_USERS)
            op.drop_column(TABLE_USERS, "vk_user_id")
        if "email_verified" in columns:
            op.drop_column(TABLE_USERS, "email_verified")
