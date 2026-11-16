from alembic import context
from sqlalchemy import create_engine

from app.storage import configured_database_url, metadata

url = (context.config.attributes.get('database_url')
       or configured_database_url())

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
