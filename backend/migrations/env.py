"""Alembic environment. The database URL comes from DATABASE_URL / PG* (same as the app), never from alembic.ini."""
from __future__ import annotations

from alembic import context
from sqlalchemy import create_engine, pool

from app.config import _database_url
from app.db import metadata

config = context.config
target_metadata = metadata


def _url() -> str:
    return config.attributes.get("url") or _database_url()


def run_migrations_offline() -> None:
    context.configure(url=_url(), target_metadata=target_metadata, literal_binds=True, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_url(), poolclass=pool.NullPool)
    with engine.connect() as conn:
        # render_as_batch: SQLite cannot ALTER most things in place; batch mode copies the table safely.
        context.configure(connection=conn, target_metadata=target_metadata, render_as_batch=True, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
