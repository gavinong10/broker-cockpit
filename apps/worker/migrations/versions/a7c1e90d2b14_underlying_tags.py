"""underlying tags

Revision ID: a7c1e90d2b14
Revises: 3f8ad2283ded
Create Date: 2026-07-11

NOTE: parented on main's head (baskets). Concurrent branches carry sibling
migrations with the same parent (e.g. journal_entries) — whichever lands
second must reparent onto the other or `alembic upgrade head` will refuse
to run with multiple heads.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "a7c1e90d2b14"
down_revision = "3f8ad2283ded"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "underlying_tags",
        sa.Column("underlying", sa.String(length=32), nullable=False),
        sa.Column("tags", postgresql.JSONB(astext_type=sa.Text()), nullable=False,
                  server_default=sa.text("'[]'::jsonb")),
        sa.PrimaryKeyConstraint("underlying"),
    )


def downgrade() -> None:
    op.drop_table("underlying_tags")
