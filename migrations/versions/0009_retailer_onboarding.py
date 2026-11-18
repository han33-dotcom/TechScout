"""Automatic retailer onboarding: lead queue, versioned capability activations, history."""
from alembic import op
import sqlalchemy as sa
from app.storage import UtcDateTime

revision = '0009'
down_revision = '0008'
branch_labels = None
depends_on = None


def upgrade():
    guard = op.create_table('onboarding_guard',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=False))
    op.bulk_insert(guard, [{'id': 1}])
    op.create_table('onboarding_leads',
        sa.Column('lead_id', sa.Text(), primary_key=True),
        sa.Column('status', sa.String(12), nullable=False),
        sa.Column('priority', sa.Integer(), nullable=False),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('next_attempt_at', UtcDateTime(), nullable=False),
        sa.Column('lease_token', sa.String(32)),
        sa.Column('lease_until', UtcDateTime()),
        sa.Column('updated_at', UtcDateTime(), nullable=False),
        sa.Column('payload', sa.JSON(), nullable=False))
    op.create_index('ix_onboarding_leads_status', 'onboarding_leads', ['status'])
    op.create_table('source_activations',
        sa.Column('source_id', sa.String(100), primary_key=True),
        sa.Column('capability_id', sa.String(100), primary_key=True),
        sa.Column('version', sa.String(100), primary_key=True),
        sa.Column('status', sa.String(12), nullable=False),
        sa.Column('activated_at', UtcDateTime(), nullable=False),
        sa.Column('changed_at', UtcDateTime(), nullable=False),
        sa.Column('payload', sa.JSON(), nullable=False))
    op.create_index('ix_source_activations_status', 'source_activations', ['status'])
    op.create_table('onboarding_events',
        sa.Column('event_id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('lead_id', sa.Text(), nullable=False),
        sa.Column('source_id', sa.String(100)),
        sa.Column('kind', sa.String(20), nullable=False),
        sa.Column('recorded_at', UtcDateTime(), nullable=False),
        sa.Column('detail', sa.JSON(), nullable=False))
    op.create_index('ix_onboarding_events_lead_id', 'onboarding_events', ['lead_id'])


def downgrade():
    for table in ('onboarding_events', 'source_activations', 'onboarding_leads', 'onboarding_guard'):
        op.drop_table(table)
