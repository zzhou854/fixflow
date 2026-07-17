"""Shared SQLAlchemy declarative base.

Domain tables are intentionally deferred until the candidate data model and
state-transition matrices are approved.
"""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Base class for future SQLAlchemy mappings."""
