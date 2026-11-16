"""Durable campaign accounting, serialized across API and worker processes."""
from alembic import op
import sqlalchemy as sa

revision = '0007'
down_revision = '0006'
branch_labels = None
depends_on = None


def upgrade():
    table = op.create_table('accounting_state',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('payload', sa.JSON(), nullable=False))
    op.bulk_insert(table, [{'id': 1, 'payload': {'project': None}}])


def downgrade():
    op.drop_table('accounting_state')
