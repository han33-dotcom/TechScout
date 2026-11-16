"""Enable the vector extension on PostgreSQL.

Revision ID: 0002
Revises: 0001
"""
from alembic import op

revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade():
    if op.get_context().dialect.name == 'postgresql':
        op.execute('CREATE EXTENSION IF NOT EXISTS vector')


def downgrade():
    # The extension may have predated this migration or be shared by other schemas.
    pass
