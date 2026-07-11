"""journal entries

Revision ID: 6cbb9d9b5f0d
Revises: 3f8ad2283ded
Create Date: 2026-07-11 13:48:21.656232

NOTE: hand-pruned after autogenerate. The local dev DB contains uncommitted
basket_plan_* tables from another in-flight session; autogenerate emitted
drops for them, which would fail on prod (where they don't exist) and
destroy someone else's work locally. This migration is journal-only.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '6cbb9d9b5f0d'
down_revision: Union[str, Sequence[str], None] = 'a7c1e90d2b14'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('journal_entries',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('symbol', sa.String(length=32), nullable=False),
    sa.Column('at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('actor', sa.String(length=320), nullable=False),
    sa.Column('tag', sa.String(length=40), nullable=False),
    sa.Column('note', sa.Text(), nullable=False),
    sa.Column('target_usd', sa.Numeric(precision=18, scale=4), nullable=True),
    sa.Column('stop_usd', sa.Numeric(precision=18, scale=4), nullable=True),
    sa.Column('confidence', sa.SmallInteger(), nullable=True),
    sa.Column('source_ref', sa.String(length=128), nullable=True),
    sa.Column('tsv', postgresql.TSVECTOR(), sa.Computed("to_tsvector('english', coalesce(note,'') || ' ' || coalesce(tag,'') || ' ' || coalesce(symbol,''))", persisted=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_journal_entries_symbol'), 'journal_entries', ['symbol'], unique=False)
    op.create_index('ix_journal_entries_tsv', 'journal_entries', ['tsv'],
                    unique=False, postgresql_using='gin')


def downgrade() -> None:
    op.drop_index('ix_journal_entries_tsv', table_name='journal_entries')
    op.drop_index(op.f('ix_journal_entries_symbol'), table_name='journal_entries')
    op.drop_table('journal_entries')
