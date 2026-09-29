"""coupons and coupon_redemptions tables with starter coupons seed.

Revision ID: 0016
Revises: 0015
Create Date: 2026-09-29
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE_COUPONS = "coupons"
TABLE_REDEMPTIONS = "coupon_redemptions"

# 5 x 500 кр (500_000_000 micro) и 5 x 2500 кр (2_500_000_000 micro)
STARTER_COUPONS = [
    # 500 кр
    ("START500", 500_000_000),
    ("STUDIO1", 500_000_000),
    ("TRY500", 500_000_000),
    ("BOOST500", 500_000_000),
    ("BONUS1", 500_000_000),
    # 2500 кр
    ("PRO2500", 2_500_000_000),
    ("PIPELINE5", 2_500_000_000),
    ("SUPER25", 2_500_000_000),
    ("VIP2500", 2_500_000_000),
    ("MEGA25", 2_500_000_000),
]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if TABLE_COUPONS not in tables:
        coupons_table = op.create_table(
            TABLE_COUPONS,
            sa.Column("id", sa.Uuid(as_uuid=False), primary_key=True),
            sa.Column("code", sa.String(length=32), nullable=False),
            sa.Column("amount_micro", sa.BigInteger(), nullable=False),
            sa.Column("max_uses", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("used_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("expires_at", sa.DateTime(), nullable=True),
        )
        op.create_index(f"ix_{TABLE_COUPONS}_code", TABLE_COUPONS, ["code"], unique=True)

        now = datetime.now(UTC).replace(tzinfo=None)
        starter_rows = [
            {
                "id": str(uuid.uuid4()),
                "code": code,
                "amount_micro": amount_micro,
                "max_uses": 1,
                "used_count": 0,
                "is_active": True,
                "created_at": now,
                "expires_at": None,
            }
            for code, amount_micro in STARTER_COUPONS
        ]
        op.bulk_insert(coupons_table, starter_rows)

    if TABLE_REDEMPTIONS not in tables:
        op.create_table(
            TABLE_REDEMPTIONS,
            sa.Column("id", sa.Uuid(as_uuid=False), primary_key=True),
            sa.Column(
                "coupon_id",
                sa.Uuid(as_uuid=False),
                sa.ForeignKey(f"{TABLE_COUPONS}.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("tenant_id", sa.Uuid(as_uuid=False), nullable=False),
            sa.Column("user_id", sa.Uuid(as_uuid=False), nullable=True),
            sa.Column("amount_micro", sa.BigInteger(), nullable=False),
            sa.Column("redeemed_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )
        op.create_index(f"ix_{TABLE_REDEMPTIONS}_coupon_id", TABLE_REDEMPTIONS, ["coupon_id"])
        op.create_index(f"ix_{TABLE_REDEMPTIONS}_tenant_id", TABLE_REDEMPTIONS, ["tenant_id"])
        op.create_index(
            f"ix_{TABLE_REDEMPTIONS}_coupon_tenant",
            TABLE_REDEMPTIONS,
            ["coupon_id", "tenant_id"],
            unique=True,
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if TABLE_REDEMPTIONS in tables:
        op.drop_table(TABLE_REDEMPTIONS)
    if TABLE_COUPONS in tables:
        op.drop_table(TABLE_COUPONS)
