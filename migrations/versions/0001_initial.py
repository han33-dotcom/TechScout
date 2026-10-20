"""Create sources, variants, snapshots, evidence, offers, runs and actions.

Revision ID: 0001
Revises:
"""
from alembic import op
import sqlalchemy as sa

from app.storage import RUN_STATES, ExactMoney, UtcDateTime

revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'sources',
        sa.Column('id', sa.String(100), nullable=False),
        sa.Column('name', sa.Text, nullable=False),
        sa.Column('origin', sa.Text, nullable=False),
        sa.Column('status', sa.String(20), nullable=False),
        sa.Column('reviewed_at', sa.Date, nullable=False),
        sa.Column('policy', sa.JSON, nullable=False),
        sa.Column('updated_at', UtcDateTime, nullable=False),
        sa.PrimaryKeyConstraint('id', name='pk_sources'),
    )
    op.create_table(
        'variants',
        sa.Column('id', sa.Integer, nullable=False),
        sa.Column('source_id', sa.String(100), nullable=False),
        sa.Column('product_id', sa.Text, nullable=False),
        sa.Column('variant_id', sa.Text, nullable=False),
        sa.Column('payload', sa.JSON, nullable=False),
        sa.PrimaryKeyConstraint('id', name='pk_variants'),
        sa.ForeignKeyConstraint(['source_id'], ['sources.id'], name='fk_variants_source_id'),
        sa.UniqueConstraint('source_id', 'product_id', 'variant_id', name='uq_variants_source_id'),
    )
    op.create_table(
        'snapshots',
        sa.Column('sha256', sa.String(64), nullable=False),
        sa.Column('size_bytes', sa.Integer, nullable=False),
        sa.Column('media_type', sa.String(100), nullable=False),
        sa.Column('stored_at', UtcDateTime, nullable=False),
        sa.PrimaryKeyConstraint('sha256', name='pk_snapshots'),
    )
    op.create_table(
        'evidence',
        sa.Column('evidence_id', sa.String(200), nullable=False),
        sa.Column('source_id', sa.String(100), nullable=False),
        sa.Column('product_id', sa.Text),
        sa.Column('variant_id', sa.Text),
        sa.Column('scope', sa.String(20), nullable=False),
        sa.Column('field_path', sa.Text, nullable=False),
        sa.Column('observed_at', UtcDateTime, nullable=False),
        sa.Column('response_sha256', sa.String(64), nullable=False),
        sa.Column('payload', sa.JSON, nullable=False),
        sa.PrimaryKeyConstraint('evidence_id', name='pk_evidence'),
        sa.ForeignKeyConstraint(['source_id'], ['sources.id'], name='fk_evidence_source_id'),
    )
    op.create_index('ix_evidence_response_sha256', 'evidence', ['response_sha256'])
    op.create_table(
        'offers',
        sa.Column('offer_id', sa.String(200), nullable=False),
        sa.Column('source_id', sa.String(100), nullable=False),
        sa.Column('variant_pk', sa.Integer, nullable=False),
        sa.Column('price_amount', ExactMoney),
        sa.Column('price_currency', sa.String(3)),
        sa.Column('payload', sa.JSON, nullable=False),
        sa.Column('stored_at', UtcDateTime, nullable=False),
        sa.PrimaryKeyConstraint('offer_id', name='pk_offers'),
        sa.ForeignKeyConstraint(['source_id'], ['sources.id'], name='fk_offers_source_id'),
        sa.ForeignKeyConstraint(['variant_pk'], ['variants.id'], name='fk_offers_variant_pk'),
    )
    op.create_table(
        'offer_evidence',
        sa.Column('offer_id', sa.String(200), nullable=False),
        sa.Column('evidence_id', sa.String(200), nullable=False),
        sa.PrimaryKeyConstraint('offer_id', 'evidence_id', name='pk_offer_evidence'),
        sa.ForeignKeyConstraint(['offer_id'], ['offers.offer_id'], ondelete='CASCADE',
                                name='fk_offer_evidence_offer_id'),
        sa.ForeignKeyConstraint(['evidence_id'], ['evidence.evidence_id'],
                                name='fk_offer_evidence_evidence_id'),
    )
    op.create_table(
        'runs',
        sa.Column('run_id', sa.String(200), nullable=False),
        sa.Column('request_id', sa.Text, nullable=False),
        sa.Column('request', sa.JSON, nullable=False),
        sa.Column('status', sa.String(20), nullable=False),
        sa.Column('stop_reason', sa.Text),
        sa.Column('started_at', UtcDateTime, nullable=False),
        sa.Column('finished_at', UtcDateTime),
        sa.Column('external_calls', sa.Integer, nullable=False),
        sa.Column('planner_turns', sa.Integer, nullable=False),
        sa.Column('spend_amount', ExactMoney, nullable=False),
        sa.Column('spend_currency', sa.String(3), nullable=False),
        sa.Column('result', sa.JSON),
        sa.PrimaryKeyConstraint('run_id', name='pk_runs'),
        sa.CheckConstraint(f'status IN {RUN_STATES}', name='ck_runs_status'),
    )
    op.create_table(
        'actions',
        sa.Column('id', sa.Integer, nullable=False),
        sa.Column('run_id', sa.String(200), nullable=False),
        sa.Column('seq', sa.Integer, nullable=False),
        sa.Column('tool', sa.String(100), nullable=False),
        sa.Column('arguments', sa.JSON, nullable=False),
        sa.Column('status', sa.String(20), nullable=False),
        sa.Column('result', sa.JSON, nullable=False),
        sa.Column('evidence_ids', sa.JSON, nullable=False),
        sa.Column('duration_ms', sa.Integer, nullable=False),
        sa.Column('recorded_at', UtcDateTime, nullable=False),
        sa.PrimaryKeyConstraint('id', name='pk_actions'),
        sa.ForeignKeyConstraint(['run_id'], ['runs.run_id'], name='fk_actions_run_id'),
        sa.UniqueConstraint('run_id', 'seq', name='uq_actions_run_id'),
    )


def downgrade():
    for table in ('actions', 'runs', 'offer_evidence', 'offers', 'evidence', 'snapshots',
                  'variants', 'sources'):
        op.drop_table(table)
