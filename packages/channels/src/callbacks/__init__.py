"""Bounded callback admission shared by robot protocols."""

from .dedupe import EventDeduplicator
from .dispatch import QueuedEvent, dispatch_queued, enqueue
from .queue import EventQueue

__all__ = [
    "EventDeduplicator",
    "EventQueue",
    "QueuedEvent",
    "dispatch_queued",
    "enqueue",
]
