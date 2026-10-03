import os
from pathlib import Path

from alembic import context
from dotenv import load_dotenv
from sqlalchemy import engine_from_config, pool

from models import Base

load_dotenv(Path(__file__).resolve().parents[1] / ".env")
config = context.config
database_url = os.environ["DATABASE_URL"]
if database_url.startswith("postgresql://"):
    database_url = database_url.replace("postgresql://", "postgresql+psycopg://", 1)
config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
target_metadata = Base.metadata


def include_object(object_, name, type_, reflected, compare_to):
    # The functional full-text index is created by 0002; Alembic cannot reflect its expression reliably.
    return not (type_ == "index" and name == "ix_chunks_fts" and reflected)


def run_migrations_offline():
    context.configure(url=database_url, target_metadata=target_metadata, literal_binds=True, include_object=include_object)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    engine = engine_from_config(config.get_section(config.config_ini_section), prefix="sqlalchemy.", poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, include_object=include_object)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
