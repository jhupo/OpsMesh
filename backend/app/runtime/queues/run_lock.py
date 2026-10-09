"""Renewable Redis execution ownership for long SDK turns."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from threading import Event, Thread

from redis import Redis
from redis.exceptions import LockNotOwnedError

from backend.app.runtime.queues.execution_control import (
    ExecutionControl,
    ExecutionOwnershipLostError,
    current_execution_control,
    execution_control,
)

logger = logging.getLogger(__name__)


@contextmanager
def renewable_run_lock(redis: Redis[str], key: str, ttl_seconds: int) -> Iterator[bool]:
    if ttl_seconds < 1:
        raise ValueError("Run lock TTL must be positive")
    lock = redis.lock(key, timeout=ttl_seconds, blocking=False, thread_local=False)
    if not lock.acquire():
        yield False
        return
    control = current_execution_control() or ExecutionControl()
    stopped = Event()

    def renew() -> None:
        while not stopped.wait(ttl_seconds / 3):
            try:
                lock.extend(ttl_seconds, replace_ttl=True)
            except Exception:
                control.ownership_lost.set()
                logger.exception("Run lock renewal failed")
                return

    thread = Thread(target=renew, name="opsmesh-run-ownership", daemon=True)
    thread.start()
    try:
        with execution_control(control):
            yield True
            control.check_ownership()
            try:
                owned = lock.owned()
            except Exception as exc:
                control.ownership_lost.set()
                raise ExecutionOwnershipLostError("Run lock ownership cannot be confirmed") from exc
            if not owned:
                control.ownership_lost.set()
                raise ExecutionOwnershipLostError("Run lock was lost before completion")
    finally:
        stopped.set()
        thread.join(timeout=max(1.0, ttl_seconds / 3))
        try:
            lock.release()
        except LockNotOwnedError:
            control.ownership_lost.set()
        except Exception as exc:
            control.ownership_lost.set()
            raise ExecutionOwnershipLostError("Run lock release failed") from exc
