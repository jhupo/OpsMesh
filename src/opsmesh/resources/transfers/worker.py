"""A native bounded Activity Worker for archive ZIP and object storage I/O."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from temporalio.client import Client
from temporalio.worker import Worker

from opsmesh.resources.transfers.activities import ArchiveExecutionActivities
from opsmesh.runtime.temporal.activities import ActivityDependencies
from opsmesh.shared.config import Settings
from opsmesh.shared.db.session import SessionLocal


def build_archive_temporal_worker(
    client: Client, *, settings: Settings, activity_executor: ThreadPoolExecutor
) -> Worker:
    activities = ArchiveExecutionActivities(
        ActivityDependencies(SessionLocal, settings), activity_executor
    )
    return Worker(
        client,
        task_queue=settings.temporal_task_queue + "-archives",
        identity=settings.temporal_worker_identity,
        activities=[activities.export, activities.verify, activities.restore_drill],
        max_concurrent_activities=settings.temporal_archive_activity_concurrency,
        max_heartbeat_throttle_interval=timedelta(seconds=5),
        graceful_shutdown_timeout=timedelta(seconds=settings.temporal_shutdown_grace_seconds),
    )
