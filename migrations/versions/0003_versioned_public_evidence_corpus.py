"""Versioned public evidence corpus

Revision ID: 0003
Revises: 0002
"""
from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import VECTOR

from app.storage import ExactMoney, UtcDateTime

revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('corpus_guard',
        sa.Column('id', sa.Integer(), autoincrement=False, nullable=False),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_corpus_guard'))
    )
    op.execute('INSERT INTO corpus_guard (id) VALUES (1)')
    op.create_table('corpus_resources',
        sa.Column('resource_id', sa.String(length=64), nullable=False),
        sa.Column('source_id', sa.String(length=100), nullable=False),
        sa.Column('resource_key', sa.JSON(), nullable=False),
        sa.Column('checked_at', UtcDateTime(), nullable=False),
        sa.Column('check_status', sa.String(length=16), nullable=False),
        sa.Column('validated_at', UtcDateTime(), nullable=True),
        sa.Column('content_changed_at', UtcDateTime(), nullable=True),
        sa.Column('etag', sa.Text(), nullable=True),
        sa.Column('last_modified', sa.Text(), nullable=True),
        sa.Column('needs_processing', sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(['source_id'], ['sources.id'], name=op.f('fk_corpus_resources_source_id')),
        sa.PrimaryKeyConstraint('resource_id', name=op.f('pk_corpus_resources'))
    )
    op.create_index(op.f('ix_corpus_resources_source_id'), 'corpus_resources', ['source_id'], unique=False)
    op.create_table('corpus_documents',
        sa.Column('document_id', sa.Integer(), nullable=False),
        sa.Column('resource_id', sa.String(length=64), nullable=False),
        sa.Column('final_url', sa.Text(), nullable=False),
        sa.Column('observed_at', UtcDateTime(), nullable=False),
        sa.Column('raw_sha256', sa.String(length=64), nullable=False),
        sa.Column('snapshot_sha256', sa.String(length=64), nullable=True),
        sa.Column('snapshot_observed_at', UtcDateTime()),
        sa.Column('relevant_sha256', sa.String(length=64), nullable=False),
        sa.Column('origin', sa.String(length=16), nullable=False),
        sa.Column('processing', sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(['resource_id'], ['corpus_resources.resource_id'], name=op.f('fk_corpus_documents_resource_id'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('document_id', name=op.f('pk_corpus_documents')),
        sqlite_autoincrement=True,
    )
    op.create_index(op.f('ix_corpus_documents_observed_at'), 'corpus_documents', ['observed_at'], unique=False)
    op.create_index('ix_corpus_documents_resource_version', 'corpus_documents', ['resource_id', 'document_id'], unique=False)
    op.create_index(op.f('ix_corpus_documents_snapshot_sha256'), 'corpus_documents', ['snapshot_sha256'], unique=False)
    op.create_table(
        'corpus_receipts',
        sa.Column('evidence_id', sa.Text(), primary_key=True),
        sa.Column('source_document_id', sa.Integer(), nullable=False),
        sa.Column('payload', sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(['source_document_id'], ['corpus_documents.document_id'],
                                name='fk_corpus_receipts_source_document_id', ondelete='CASCADE'),
    )
    op.create_index('ix_corpus_receipts_source_document_id', 'corpus_receipts', ['source_document_id'])
    op.create_table('corpus_observations',
        sa.Column('observation_id', sa.Integer(), nullable=False),
        sa.Column('source_document_id', sa.Integer(), nullable=False),
        sa.Column('field_key', sa.String(length=64), nullable=False),
        sa.Column('scope', sa.String(length=20), nullable=False),
        sa.Column('product_id', sa.Text(), nullable=True),
        sa.Column('variant_id', sa.Text(), nullable=True),
        sa.Column('branch_id', sa.Text(), nullable=True),
        sa.Column('route_id', sa.Text(), nullable=True),
        sa.Column('field_path', sa.Text(), nullable=False),
        sa.Column('observed_at', UtcDateTime(), nullable=False),
        sa.Column('fact_state', sa.String(length=12), nullable=False),
        sa.Column('fact_value', sa.JSON(), nullable=True),
        sa.Column('price_amount', ExactMoney(), nullable=True),
        sa.Column('price_currency', sa.String(length=3), nullable=True),
        sa.Column('field_sha256', sa.String(length=64), nullable=False),
        sa.Column('payload', sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(['source_document_id'], ['corpus_documents.document_id'], name=op.f('fk_corpus_observations_source_document_id'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('observation_id', name=op.f('pk_corpus_observations')),
        sqlite_autoincrement=True,
    )
    op.create_index('ix_corpus_observations_product_field', 'corpus_observations', ['product_id', 'variant_id', 'field_path'], unique=False)
    op.create_index(op.f('ix_corpus_observations_source_document_id'), 'corpus_observations', ['source_document_id'], unique=False)
    op.create_table('corpus_passages',
        sa.Column('passage_id', sa.Integer(), nullable=False),
        sa.Column('source_document_id', sa.Integer(), nullable=False),
        sa.Column('passage_key', sa.String(length=64), nullable=False),
        sa.Column('passage_sha256', sa.String(length=64), nullable=False),
        sa.Column('text', sa.Text(), nullable=False),
        sa.Column('payload', sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(['source_document_id'], ['corpus_documents.document_id'], name=op.f('fk_corpus_passages_source_document_id'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('passage_id', name=op.f('pk_corpus_passages')),
        sqlite_autoincrement=True,
    )
    op.create_index(op.f('ix_corpus_passages_source_document_id'), 'corpus_passages', ['source_document_id'], unique=False)
    op.create_table('corpus_document_observations',
        sa.Column('document_id', sa.Integer(), nullable=False),
        sa.Column('observation_id', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['document_id'], ['corpus_documents.document_id'], name=op.f('fk_corpus_document_observations_document_id'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['observation_id'], ['corpus_observations.observation_id'], name=op.f('fk_corpus_document_observations_observation_id'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('document_id', 'observation_id', name=op.f('pk_corpus_document_observations'))
    )
    op.create_index(op.f('ix_corpus_document_observations_observation_id'), 'corpus_document_observations', ['observation_id'], unique=False)
    op.create_table('corpus_document_passages',
        sa.Column('document_id', sa.Integer(), nullable=False),
        sa.Column('passage_id', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['document_id'], ['corpus_documents.document_id'], name=op.f('fk_corpus_document_passages_document_id'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['passage_id'], ['corpus_passages.passage_id'], name=op.f('fk_corpus_document_passages_passage_id'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('document_id', 'passage_id', name=op.f('pk_corpus_document_passages'))
    )
    op.create_index(op.f('ix_corpus_document_passages_passage_id'), 'corpus_document_passages', ['passage_id'], unique=False)
    op.create_table('corpus_embeddings',
        sa.Column('passage_id', sa.Integer(), nullable=False),
        sa.Column('index_version', sa.Text(), nullable=False),
        sa.Column('model', sa.Text(), nullable=False),
        sa.Column('dimensions', sa.Integer(), nullable=False),
        sa.Column('embedding', VECTOR().with_variant(sa.JSON(), 'sqlite'), nullable=False),
        sa.CheckConstraint('dimensions BETWEEN 1 AND 2000', name=op.f('ck_corpus_embeddings_dimensions')),
        sa.ForeignKeyConstraint(['passage_id'], ['corpus_passages.passage_id'], name=op.f('fk_corpus_embeddings_passage_id'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('passage_id', 'index_version', 'model', 'dimensions', name=op.f('pk_corpus_embeddings'))
    )
    op.create_index('ix_corpus_embeddings_profile', 'corpus_embeddings', ['index_version', 'model', 'dimensions'], unique=False)

    if op.get_context().dialect.name == 'postgresql':
        op.execute("CREATE INDEX ix_corpus_passages_fulltext ON corpus_passages "
                   "USING gin (to_tsvector('simple'::regconfig, text))")
        op.execute('CREATE INDEX ix_corpus_embeddings_hnsw ON corpus_embeddings '
                   'USING hnsw ((embedding::vector(1536)) vector_cosine_ops) WHERE dimensions = 1536')


def downgrade():
    for table in ('corpus_embeddings', 'corpus_document_passages', 'corpus_document_observations',
                  'corpus_passages', 'corpus_observations', 'corpus_receipts', 'corpus_documents',
                  'corpus_resources', 'corpus_guard'):
        op.drop_table(table)
