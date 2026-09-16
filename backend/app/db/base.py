"""Declarative base plus column types that work on both Postgres and SQLite."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, MetaData, TypeDecorator, Uuid
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, mapped_column

# Explicit naming convention keeps generated constraint names stable across
# migrations instead of relying on per-backend defaults.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

# JSONB on Postgres (indexable, typed) and plain JSON on SQLite.
JSONType = JSON().with_variant(JSONB, "postgresql")


class UTCDateTime(TypeDecorator):
    """Timestamps that are always timezone-aware UTC on the way in and out.

    Postgres stores the offset; SQLite has no timezone support and hands back
    naive datetimes, so a value written as aware would come back naive and blow
    up the first time it was compared against ``datetime.now(timezone.utc)``.
    Normalising here means every consumer - services, API responses, the
    recovery pass - sees the same thing on both backends.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


TimestampType = UTCDateTime()


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def utcnow() -> datetime:
    return datetime.now(UTC)


def uuid_pk():
    return mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)


def created_at_col():
    return mapped_column(TimestampType, default=utcnow, nullable=False, index=True)


def updated_at_col():
    return mapped_column(TimestampType, default=utcnow, onupdate=utcnow, nullable=False)


def enum_column(enum_cls, **kwargs):
    """A portable enum column.

    ``native_enum=False`` stores the value as VARCHAR with a CHECK constraint
    rather than a Postgres ENUM type, which keeps SQLite and Postgres identical
    and avoids an ALTER TYPE migration for every new member. ``values_callable``
    persists the member *values* ("in_progress"), not the member names.
    """
    return mapped_column(
        SAEnum(
            enum_cls,
            native_enum=False,
            length=32,
            values_callable=lambda e: [member.value for member in e],
            validate_strings=True,
        ),
        **kwargs,
    )
