"""Disposable wakeups; the queue and durable leases still decide who may claim work."""

import asyncio
from time import monotonic

from redis.client import PubSub


async def wait_for_notification(subscription: PubSub, timeout: float) -> None:
    """Wait without a DB transaction. A timeout permits retry recovery and shutdown checks."""
    pending = asyncio.create_task(asyncio.to_thread(_wait, subscription, timeout))
    try:
        await asyncio.shield(pending)
    except asyncio.CancelledError:
        # PubSub is not thread-safe: finish its pending socket read before closing it.
        await pending
        raise


def _wait(subscription: PubSub, timeout: float) -> None:
    deadline = monotonic() + timeout
    while (remaining := deadline - monotonic()) > 0:
        item = subscription.get_message(timeout=remaining)
        if item is not None and item["type"] == "message":
            return
