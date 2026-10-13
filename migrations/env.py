import os

from alembic import context
from sqlalchemy import create_engine

from app.storage import DEFAULT_DATABASE_URL, metadata

url = (context.config.attributes.get('database_url')
       or os.environ.get('DATABASE_URL', DEFAULT_DATABASE_URL))

if context.is_offline_mode():
    context.configure(url=url, target_metadata=metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    engine = create_engine(url)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=metadata,
                          render_as_batch=connection.dialect.name == 'sqlite')
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()
