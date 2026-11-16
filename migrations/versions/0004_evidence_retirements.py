"""Keep supported removal evidence separately from transient check failures."""
from alembic import op
import sqlalchemy as sa
from app.storage import UtcDateTime

revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('corpus_retirements',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('resource_id', sa.String(64), sa.ForeignKey('corpus_resources.resource_id', ondelete='CASCADE'), nullable=False),
        sa.Column('observed_at', UtcDateTime(), nullable=False),
        sa.Column('status', sa.String(16), nullable=False),
        sa.Column('evidence', sa.JSON(), nullable=False))
    op.create_index('ix_corpus_retirements_resource_time', 'corpus_retirements', ['resource_id', 'observed_at'])


def downgrade():
    op.drop_table('corpus_retirements')
