"""snapshots.source: observed vs backfilled history

Revision ID: c3d5e7f90a12
Revises: b9e2d4a71c05
Create Date: 2026-07-11
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c3d5e7f90a12"
down_revision: Union[str, Sequence[str], None] = "b9e2d4a71c05"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("snapshots", sa.Column(
        "source", sa.String(length=16), server_default="observed", nullable=False))


def downgrade() -> None:
    op.drop_column("snapshots", "source")
