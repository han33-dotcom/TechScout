"""Pre-order delivery quotes for buyer chats (#186)."""
from alembic import op
import sqlalchemy as sa
from app.storage import UtcDateTime

revision = '0011'
down_revision = '0010'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('buyer_quotes',
        sa.Column('quote_id', sa.String(36), primary_key=True),
        sa.Column('session_hash', sa.String(64),
                  sa.ForeignKey('buyer_sessions.session_hash', ondelete='CASCADE'), nullable=False),
        sa.Column('request_id', sa.Text(), nullable=False),
        sa.Column('run_id', sa.String(36), sa.ForeignKey('buyer_runs.run_id', ondelete='CASCADE'), nullable=False),
        sa.Column('request_key', sa.String(64), nullable=False),
        sa.Column('status', sa.String(12), nullable=False),
        sa.Column('record', sa.JSON(), nullable=False),
        sa.Column('lease_token', sa.String(32)),
        sa.Column('lease_until', UtcDateTime()),
        sa.Column('created_at', UtcDateTime(), nullable=False),
        sa.Column('updated_at', UtcDateTime(), nullable=False),
        sa.UniqueConstraint('session_hash', 'request_id', 'request_key', name='uq_buyer_quote_request'))
    op.create_index('ix_buyer_quotes_run_id', 'buyer_quotes', ['run_id'])
    op.create_index('ix_buyer_quotes_status', 'buyer_quotes', ['status'])
    op.create_table('buyer_quote_plans',
        sa.Column('run_id', sa.String(36), sa.ForeignKey('buyer_runs.run_id', ondelete='CASCADE'), primary_key=True),
        sa.Column('decided_at', UtcDateTime()),
        sa.Column('planned_at', UtcDateTime(), nullable=False))


def downgrade():
    op.drop_table('buyer_quote_plans')
    op.drop_table('buyer_quotes')
