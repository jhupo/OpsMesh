from __future__ import annotations

from dataclasses import dataclass

from backend.app.workers.jobs import JobPayload


@dataclass(frozen=True, slots=True)
class QueueInsightScan:
    queued_total: int
    dead_letter_total: int
    queued_jobs: list[JobPayload]
    dead_letter_jobs: list[JobPayload]

    @property
    def truncated(self) -> bool:
        return self.queued_total > len(self.queued_jobs) or self.dead_letter_total > len(
            self.dead_letter_jobs
        )
