from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ScheduledLifecycleSummary:
    scanned_workspaces: int = 0
    backup_jobs_enqueued: int = 0
    backup_jobs_skipped: int = 0
    retention_runs_applied: int = 0
    retention_runs_skipped: int = 0
    restore_drills_completed: int = 0
    restore_drills_skipped: int = 0
    details: list[dict[str, object]] = field(default_factory=list)

    def combine(self, other: ScheduledLifecycleSummary) -> ScheduledLifecycleSummary:
        return ScheduledLifecycleSummary(
            scanned_workspaces=self.scanned_workspaces + other.scanned_workspaces,
            backup_jobs_enqueued=self.backup_jobs_enqueued + other.backup_jobs_enqueued,
            backup_jobs_skipped=self.backup_jobs_skipped + other.backup_jobs_skipped,
            retention_runs_applied=self.retention_runs_applied + other.retention_runs_applied,
            retention_runs_skipped=self.retention_runs_skipped + other.retention_runs_skipped,
            restore_drills_completed=(
                self.restore_drills_completed + other.restore_drills_completed
            ),
            restore_drills_skipped=self.restore_drills_skipped + other.restore_drills_skipped,
            details=[*self.details, *other.details],
        )

    def to_metadata(self) -> dict[str, object]:
        return {
            "scanned_workspaces": self.scanned_workspaces,
            "backup_jobs_enqueued": self.backup_jobs_enqueued,
            "backup_jobs_skipped": self.backup_jobs_skipped,
            "retention_runs_applied": self.retention_runs_applied,
            "retention_runs_skipped": self.retention_runs_skipped,
            "restore_drills_completed": self.restore_drills_completed,
            "restore_drills_skipped": self.restore_drills_skipped,
            "details": self.details,
        }
