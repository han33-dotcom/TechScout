"""Canonical retailer directory with bounded market/category lookup."""
from alembic import op
import sqlalchemy as sa
from app.storage import UtcDateTime

revision = '0006'
down_revision = '0005'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('directory_entries',
        sa.Column('retailer_id',sa.Text(),primary_key=True),
        sa.Column('updated_at',UtcDateTime(),nullable=False),
        sa.Column('payload',sa.JSON(),nullable=False))
    for table,field,type_ in (
        ('directory_categories','category',sa.String(20)),
        ('directory_markets','market',sa.String(2)),
        ('directory_aliases','alias',sa.Text()),
    ):
        op.create_table(table,
            sa.Column('retailer_id',sa.Text(),sa.ForeignKey('directory_entries.retailer_id',ondelete='CASCADE'),primary_key=True),
            sa.Column(field,type_,primary_key=True))
        op.create_index(f'ix_{table}_{field}',table,[field])


def downgrade():
    for table in ('directory_aliases','directory_markets','directory_categories','directory_entries'):
        op.drop_table(table)
