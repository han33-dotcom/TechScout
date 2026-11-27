"""Store each accounting record independently and preserve the version 1 snapshot."""
from alembic import op
from sqlalchemy import select, update

from app.ledger_storage import entries, materialize, migrate_rows, read_state
from app.storage import accounting_state

revision = 'exp_ledger_rows'
down_revision = '0010'
branch_labels = None
depends_on = None


def upgrade():
    db = op.get_bind()
    migrate_rows(db)


def downgrade():
    db = op.get_bind()
    row = accounting_state.c.id == 1
    header = db.execute(select(accounting_state.c.payload).where(row).with_for_update()).scalar_one()
    state = materialize(read_state(db, header))
    db.execute(update(accounting_state).where(row).values(payload=state))
    entries.drop(db)
