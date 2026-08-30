"""SQLAlchemy declarative base and shared database conventions.

The domain package deliberately does not import SQLAlchemy.  Database-facing
code lives under :mod:`app.db` and is kept as a persistence representation of
the authoritative run state.
"""

from __future__ import annotations

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Declarative base used by the Alembic target metadata."""

    metadata = MetaData(
        naming_convention={
            "ix": "ix_%(table_name)s_%(column_0_name)s",
            "uq": "uq_%(table_name)s_%(column_0_name)s",
            "ck": "ck_%(table_name)s_%(column_0_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        }
    )


__all__ = ["Base"]
