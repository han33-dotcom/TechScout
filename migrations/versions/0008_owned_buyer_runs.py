"""Owned buyer sessions and durable worker/run coordination."""
from alembic import op
import sqlalchemy as sa
from app.storage import UtcDateTime

revision = '0008'
down_revision = '0007'
branch_labels = None
depends_on = None


def upgrade():
    guard=op.create_table('buyer_guard',sa.Column('id',sa.Integer(),primary_key=True,autoincrement=False))
    op.bulk_insert(guard,[{'id':1}])
    op.create_table('buyer_sessions',
        sa.Column('session_hash',sa.String(64),primary_key=True),
        sa.Column('csrf',sa.String(64),nullable=False),
        sa.Column('expires_at',UtcDateTime(),nullable=False))
    op.create_index('ix_buyer_sessions_expires_at','buyer_sessions',['expires_at'])
    op.create_table('buyer_runs',
        sa.Column('run_id',sa.String(36),primary_key=True),
        sa.Column('session_hash',sa.String(64),sa.ForeignKey('buyer_sessions.session_hash',ondelete='CASCADE'),nullable=False),
        sa.Column('request_id',sa.Text(),nullable=False),
        sa.Column('request_revision',sa.Integer(),nullable=False),
        sa.Column('status',sa.String(20),nullable=False),
        sa.Column('snapshot',sa.JSON(),nullable=False),
        sa.Column('answers',sa.JSON(),nullable=False),
        sa.Column('previous_id',sa.String(36)),
        sa.Column('lease_token',sa.String(32)),
        sa.Column('lease_until',UtcDateTime()),
        sa.Column('active_since',UtcDateTime()),
        sa.Column('active_ms',sa.Integer(),nullable=False),
        sa.UniqueConstraint('session_hash','request_id','request_revision',name='uq_buyer_request_revision'))
    op.create_index('ix_buyer_runs_session_hash','buyer_runs',['session_hash'])
    op.create_index('ix_buyer_runs_status','buyer_runs',['status'])


def downgrade():
    op.drop_table('buyer_runs')
    op.drop_table('buyer_sessions')
    op.drop_table('buyer_guard')
