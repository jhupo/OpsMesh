from dataclasses import dataclass


@dataclass(frozen=True)
class WorkerEventDispatchSummary:
    task_events_published: int = 0
    task_event_publish_failures: int = 0
