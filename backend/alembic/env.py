"""Alembic environment.

The database URL comes from application settings rather than alembic.ini, so
migrations always target the same database the app does and there is one place
to configure it.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.ext.asyncio import async_engine_from_config
from sqlalchemy import pool

from app.core.config import get_settings
from app.db.base import Base
from app.db import models  # noqa: F401 - registers the tables on Base.metadata

config = context.config
config.set_main_option("sqlalchemy.url", get_settings().database_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _render_item(type_, obj, autogen_context) -> str | bool:
    """Render the JSON/JSONB variant column with fully qualified names.

    Two of the project's column types do not render into runnable code on
    their own: Alembic emits ``postgresql.JSONB(astext_type=Text())`` with
    ``Text`` unqualified, and renders the custom ``UTCDateTime`` as a dotted
    path it never imports. Both raise NameError the moment the migration runs.
    """
    import sqlalchemy as sa

    from app.db.base import UTCDateTime

    if type_ == "type" and isinstance(obj, UTCDateTime):
        # The custom type only normalises values in Python; the column the
        # database sees is a plain timezone-aware timestamp.
        autogen_context.imports.add("import sqlalchemy as sa")
        return "sa.DateTime(timezone=True)"

    if type_ == "type" and isinstance(obj, sa.JSON):
        autogen_context.imports.add("import sqlalchemy as sa")
        autogen_context.imports.add("from sqlalchemy.dialects import postgresql")
        return (
            "sa.JSON().with_variant("
            "postgresql.JSONB(astext_type=sa.Text()), 'postgresql')"
        )
    return False


def _configure(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_item=_render_item,
        # SQLite cannot ALTER most things in place; batch mode rebuilds the
        # table instead, so the same migration runs on both backends.
        render_as_batch=connection.dialect.name == "sqlite",
        compare_type=True,
        compare_server_default=True,
    )


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        render_item=_render_item,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def _run_migrations(connection) -> None:
    """Configure and run inside one sync callable, in one transaction.

    Splitting these across two `run_sync` calls, without an explicit
    `begin_transaction`, leaves Postgres to roll the whole migration back when
    the connection closes - it reports success and creates nothing. SQLite
    autocommits DDL, so the mistake is invisible there.
    """
    _configure(connection)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
