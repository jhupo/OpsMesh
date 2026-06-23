from __future__ import annotations

from dataclasses import dataclass, field

from backend.app.api.schemas.operation_queue import StaleRunRecoveryItemResponse


@dataclass(slots=True)
class StaleRunRecoveryCounts:
    requeued: int = 0
    failed_closed: int = 0
    items: list[StaleRunRecoveryItemResponse] = field(default_factory=list)
