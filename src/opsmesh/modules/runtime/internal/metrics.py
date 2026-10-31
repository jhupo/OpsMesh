"""Observe committed state transitions; metrics failure cannot roll back run facts."""

from __future__ import annotations

import logging

from opsmesh.infrastructure.database import StoredRecord
from opsmesh.modules.runtime.api import TERMINAL_STATES, MetricObserver

logger = logging.getLogger(__name__)


class RunMetrics:
    def __init__(self, observer: MetricObserver | None) -> None:
        self.observer = observer

    def transition(self, before: StoredRecord, after: StoredRecord) -> None:
        if self.observer is None or before.data["status"] == after.data["status"]:
            return
        previous, current = before.data["status"], after.data["status"]
        now = after.updated_at.timestamp()
        observations: list[tuple[str, float]] = []
        if previous in {"queued", "resuming"} and current == "preparing":
            started = before.data.get("queued_at", before.created_at.timestamp())
            observations.append(("queue_wait_seconds", max(0, now - started)))
        if previous == "waiting_approval":
            started = before.data.get("approval_wait_started_at", before.updated_at.timestamp())
            observations.append(("approval_wait_seconds", max(0, now - started)))
        if current in TERMINAL_STATES and previous not in TERMINAL_STATES:
            observations.append(
                ("run_duration_seconds", max(0, now - before.created_at.timestamp()))
            )
        for name, seconds in observations:
            try:
                self.observer(name, seconds)
            except Exception:
                logger.exception("Run metric observation failed: %s", name)
