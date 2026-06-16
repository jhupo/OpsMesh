from __future__ import annotations

from dataclasses import dataclass


@dataclass
class QueueGovernanceReconcileCounts:
    requeued_missing_runs: int = 0
    removed_orphaned_jobs: int = 0
    removed_non_runnable_jobs: int = 0
    skipped_items: int = 0
    remaining: int = 0
