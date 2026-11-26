"""TechScout buyer accounts, sign-ins, private saved locations and chat destinations (#187)."""
from alembic import op
import sqlalchemy as sa
from app.storage import UtcDateTime

revision = '0010'
down_revision = '0009'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('buyer_accounts',
        sa.Column('account_id', sa.String(36), primary_key=True),
        sa.Column('username', sa.String(32), nullable=False, unique=True),
        sa.Column('password_hash', sa.String(255), nullable=False),
        sa.Column('created_at', UtcDateTime(), nullable=False))
    op.create_table('buyer_signins',
        sa.Column('session_hash', sa.String(64),
                  sa.ForeignKey('buyer_sessions.session_hash', ondelete='CASCADE'), primary_key=True),
        sa.Column('account_id', sa.String(36),
                  sa.ForeignKey('buyer_accounts.account_id', ondelete='CASCADE'), nullable=False),
        sa.Column('signed_in_at', UtcDateTime(), nullable=False))
    op.create_index('ix_buyer_signins_account_id', 'buyer_signins', ['account_id'])
    op.create_table('buyer_locations',
        sa.Column('location_id', sa.String(36), primary_key=True),
        sa.Column('account_id', sa.String(36),
                  sa.ForeignKey('buyer_accounts.account_id', ondelete='CASCADE'), nullable=False),
        sa.Column('label', sa.String(40), nullable=False),
        sa.Column('city', sa.String(80), nullable=False),
        sa.Column('street', sa.String(200), nullable=False),
        sa.Column('details', sa.String(200)),
        sa.Column('created_at', UtcDateTime(), nullable=False))
    op.create_index('ix_buyer_locations_account_id', 'buyer_locations', ['account_id'])
    op.create_table('buyer_destinations',
        sa.Column('session_hash', sa.String(64),
                  sa.ForeignKey('buyer_sessions.session_hash', ondelete='CASCADE'), primary_key=True),
        sa.Column('request_id', sa.Text(), primary_key=True),
        sa.Column('status', sa.String(10), nullable=False),
        sa.Column('location_id', sa.String(36),
                  sa.ForeignKey('buyer_locations.location_id', ondelete='CASCADE')),
        sa.Column('recipients', sa.JSON(), nullable=False),
        sa.Column('quote_state', sa.String(12), nullable=False),
        sa.Column('decided_at', UtcDateTime(), nullable=False))
    op.create_index('ix_buyer_destinations_location_id', 'buyer_destinations', ['location_id'])
    op.create_table('buyer_attempts',
        sa.Column('key', sa.String(80), primary_key=True),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('window_start', UtcDateTime(), nullable=False))


def downgrade():
    for table in ('buyer_attempts', 'buyer_destinations', 'buyer_locations', 'buyer_signins', 'buyer_accounts'):
        op.drop_table(table)
