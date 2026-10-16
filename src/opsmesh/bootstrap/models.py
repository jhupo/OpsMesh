"""Explicitly register the ORM models owned by the current application."""

from sqlalchemy import MetaData

from opsmesh.shared.db.base import Base
from opsmesh.shared.db.idempotency import CreationReceipt
from opsmesh.temporal.dispatch.models import TemporalOutboxEntry


def register_models() -> MetaData:
    """Explicitly register the tables owned by the current application."""
    _ = (TemporalOutboxEntry, CreationReceipt)
    return Base.metadata
