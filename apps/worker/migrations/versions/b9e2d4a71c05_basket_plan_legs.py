"""basket plan legs + marks (plan monitor, task 1)

Revision ID: b9e2d4a71c05
Revises: f1e2d3c4b5a6
Create Date: 2026-07-11

Plan doc: docs/superpowers/plans/2026-07-11-basket-plan-monitor.md
Additive only — no changes to existing basket/allocation semantics.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b9e2d4a71c05"
down_revision: Union[str, Sequence[str], None] = "f1e2d3c4b5a6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "basket_plan_legs",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("basket_id", sa.BigInteger(), nullable=False),
        sa.Column("label", sa.String(length=64), nullable=False),
        sa.Column("structure", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("qty", sa.Numeric(precision=24, scale=8), nullable=False),
        sa.Column("planned_net_debit", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("tolerance_pct", sa.Numeric(precision=6, scale=3), server_default="5", nullable=False),
        sa.Column("breakeven_underlying", sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column("max_value_usd", sa.Numeric(precision=18, scale=2), nullable=True),
        sa.Column("thesis_note", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=16), server_default="pending", nullable=False),
        sa.Column("monitor_status", sa.String(length=16), nullable=True),
        sa.Column("last_quote_net", sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column("last_quoted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_alerted_status", sa.String(length=16), nullable=True),
        sa.Column("filled_net_debit", sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["basket_id"], ["baskets.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("basket_id", "label"),
    )
    op.create_table(
        "basket_plan_marks",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("plan_leg_id", sa.BigInteger(), nullable=False),
        sa.Column("taken_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("net_cost", sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column("underlying_spot", sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column("quote_basis", sa.String(length=8), nullable=True),
        sa.ForeignKeyConstraint(["plan_leg_id"], ["basket_plan_legs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_basket_plan_marks_leg_taken",
        "basket_plan_marks",
        ["plan_leg_id", "taken_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_basket_plan_marks_leg_taken", table_name="basket_plan_marks")
    op.drop_table("basket_plan_marks")
    op.drop_table("basket_plan_legs")
