"""features table (feature-factory)

Revision ID: f1e2d3c4b5a6
Revises: 6cbb9d9b5f0d
Create Date: 2026-07-11
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "f1e2d3c4b5a6"
down_revision = "6cbb9d9b5f0d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "features",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("slug", sa.String(48), nullable=False, unique=True),
        sa.Column("prompt", sa.Text, nullable=False),
        sa.Column("model", sa.String(48), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="created"),
        sa.Column("diff_stat", sa.Text),
        sa.Column("risky_paths", JSONB, server_default="[]"),
        sa.Column("merge_sha", sa.String(40)),
        sa.Column("report", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("features")
