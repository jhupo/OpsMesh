"""Durable event delivery infrastructure; routing policy belongs to the platform."""

from .outbox import TransactionalOutbox

__all__ = ["TransactionalOutbox"]
