import os
from logging.config import fileConfig
from alembic import context
from sqlalchemy import engine_from_config, pool
from app.database import Base
import app.models  # noqa: F401

config = context.config
config.set_main_option("sqlalchemy.url", os.getenv("DATABASE_URL", config.get_main_option("sqlalchemy.url")))
if config.config_file_name:
    # disable_existing_loggers=False, explicitly.
    #
    # Alembic's generated env.py calls fileConfig with Python's default,
    # disable_existing_loggers=True, which walks every logger that already
    # exists and sets `.disabled = True` on the ones the ini does not name.
    # `alembic upgrade head` runs IN-PROCESS on API startup (database.init_db),
    # so the loggers it silences are the application's own -- every
    # findback.* logger, including findback.auth.
    #
    # The effect is that the API logs nothing at all from the moment the
    # schema bootstrap runs until the process restarts: the startup log line,
    # every provider warning, and the Phase 16 auth refusals all go silent.
    # Nothing in the test suite noticed, because the only tests that read a
    # log record ran before their own fixture invoked Alembic.
    fileConfig(config.config_file_name, disable_existing_loggers=False)
target_metadata = Base.metadata


def run_migrations_offline():
    context.configure(url=config.get_main_option("sqlalchemy.url"), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    connectable = engine_from_config(config.get_section(config.config_ini_section), prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


run_migrations_offline() if context.is_offline_mode() else run_migrations_online()
