"""Durable embedding usage, including uncertain requests."""
from alembic import op
import sqlalchemy as sa
from app.storage import ExactMoney, UtcDateTime

revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('embedding_calls',
        sa.Column('call_id',sa.String(32),primary_key=True),
        sa.Column('owner_id',sa.Text(),nullable=False),
        sa.Column('purpose',sa.String(10),nullable=False),
        sa.Column('model',sa.Text(),nullable=False),
        sa.Column('index_version',sa.Text(),nullable=False),
        sa.Column('status',sa.String(12),nullable=False),
        sa.Column('reserved_usd',ExactMoney(),nullable=False),
        sa.Column('rate_usd_per_million',ExactMoney(),nullable=False),
        sa.Column('tokens',sa.Integer()),
        sa.Column('actual_usd',ExactMoney()),
        sa.Column('recorded_at',UtcDateTime(),nullable=False))
    op.create_index('ix_embedding_calls_owner_id','embedding_calls',['owner_id'])


def downgrade():
    op.drop_table('embedding_calls')
