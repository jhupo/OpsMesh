import asyncio
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, suppress
from contextvars import copy_context

from sqlalchemy import select
from sqlalchemy.orm import Session
from temporalio import activity

from opsmesh.identity.authorization.permissions import WorkspaceAction
from opsmesh.resources.storage.backends import create_storage
from opsmesh.resources.transfers.archive_restore_drill import WorkspaceArchiveRestoreDrillService
from opsmesh.resources.transfers.models import WorkspaceExportJob, WorkspaceRestoreDrillOperation
from opsmesh.resources.transfers.operation_identity import authorize_archive_identity
from opsmesh.resources.transfers.restore_operations import RestoreDrillOperationService
from opsmesh.resources.transfers.service import WorkspaceExportService
from opsmesh.resources.transfers.verification_operations import ArchiveVerificationOperationService
from opsmesh.runtime.temporal.activities import ActivityDependencies, authorize_execution
from opsmesh.runtime.temporal.contracts import ExecutionKind, ExecutionRequest


class ArchiveControlActivities:
    def __init__(self, services: ActivityDependencies) -> None:
        self._services = services

    @activity.defn(name="opsmesh.archive.prepare")
    def prepare(self, request: ExecutionRequest) -> str:
        with self._services.transaction() as session:
            status = _authorize_request(session, request)
            if status == "completed":
                return ""
            if status != "queued":
                raise ValueError("Archive operation is no longer queued")
            return self._services.settings.temporal_task_queue + "-archives"

    @activity.defn(name="opsmesh.archive.verify_failed")
    def verify_failed(self, request: ExecutionRequest) -> None:
        with self._services.transaction() as session:
            ArchiveVerificationOperationService(session).failed(request)

    @activity.defn(name="opsmesh.archive.restore_drill_interrupted")
    def restore_drill_interrupted(self, request: ExecutionRequest) -> None:
        with self._services.transaction() as session:
            row = session.scalar(
                select(WorkspaceRestoreDrillOperation)
                .where(
                    WorkspaceRestoreDrillOperation.workspace_id == request.workspace_id,
                    WorkspaceRestoreDrillOperation.id == request.resource_id,
                )
                .with_for_update()
            )
            if row is not None and row.status in {"queued", "running"}:
                status = "unknown" if row.status == "running" else "failed"
                RestoreDrillOperationService(session).finish(
                    request.workspace_id,
                    row.id,
                    status,
                    "restore_drill_outcome_unknown"
                    if status == "unknown"
                    else "restore_drill_not_started",
                )

    @activity.defn(name="opsmesh.archive.interrupted")
    def interrupted(self, request: ExecutionRequest) -> None:
        with self._services.transaction() as session:
            WorkspaceExportService(session).settle_interrupted_export(request=request)


class ArchiveExecutionActivities:
    def __init__(self, services: ActivityDependencies, executor: ThreadPoolExecutor) -> None:
        self._services, self._executor = services, executor

    @activity.defn(name="opsmesh.archive.export")
    async def export(self, request: ExecutionRequest) -> None:
        await _run_with_heartbeat(self._executor, lambda: self._export(request))

    @activity.defn(name="opsmesh.archive.verify")
    async def verify(self, request: ExecutionRequest) -> None:
        await _run_with_heartbeat(self._executor, lambda: self._verify(request))

    @activity.defn(name="opsmesh.archive.restore_drill")
    async def restore_drill(self, request: ExecutionRequest) -> None:
        await _run_with_heartbeat(self._executor, lambda: self._restore_drill(request))

    def _export(self, request: ExecutionRequest) -> None:
        with self._services.transaction() as session:
            _authorize_request(session, request)
            session.commit()
            with closing(create_storage(self._services.settings)) as storage:
                WorkspaceExportService(session).run_archive_export_job(
                    request=request, storage=storage
                )

    def _verify(self, request: ExecutionRequest) -> None:
        with self._services.transaction() as session:
            _authorize_request(session, request)
            session.commit()
            with closing(create_storage(self._services.settings)) as storage:
                ArchiveVerificationOperationService(session).run(request, storage)

    def _restore_drill(self, request: ExecutionRequest) -> None:
        with self._services.transaction() as session:
            _authorize_request(session, request)
            session.commit()
            with closing(create_storage(self._services.settings)) as storage:
                WorkspaceArchiveRestoreDrillService(session).run(execution=request, storage=storage)


def _authorize_request(session: Session, request: ExecutionRequest) -> str:
    authorize_execution(session, request, action=WorkspaceAction.READ)
    if request.kind == ExecutionKind.ARCHIVE_EXPORT:
        row = session.scalar(
            select(WorkspaceExportJob).where(
                WorkspaceExportJob.workspace_id == request.workspace_id,
                WorkspaceExportJob.id == request.resource_id,
            )
        )
        if row is None:
            raise ValueError("Archive export job not found")
        status, identity = row.status, row.execution_identity
    elif request.kind == ExecutionKind.ARCHIVE_VERIFY:
        verification = ArchiveVerificationOperationService(session).get(
            request.workspace_id, request.resource_id
        )
        status, identity = verification.status, verification.execution_identity
    elif request.kind == ExecutionKind.ARCHIVE_RESTORE_DRILL:
        restore = session.scalar(
            select(WorkspaceRestoreDrillOperation).where(
                WorkspaceRestoreDrillOperation.workspace_id == request.workspace_id,
                WorkspaceRestoreDrillOperation.id == request.resource_id,
            )
        )
        if restore is None:
            raise ValueError("Restore drill operation not found")
        status, identity = restore.status, restore.execution_identity
    else:
        raise ValueError("Archive control requires a matching execution kind")
    user = authorize_archive_identity(session, request.workspace_id, identity)
    if user.user_id != request.requested_by_user_id:
        raise ValueError("Archive operation initiating identity mismatch")
    return status


async def _run_with_heartbeat(executor: ThreadPoolExecutor, operation: Callable[[], None]) -> None:
    pending = asyncio.get_running_loop().run_in_executor(executor, copy_context().run, operation)
    try:
        while not pending.done():
            activity.heartbeat()
            await asyncio.wait({pending}, timeout=5)
        await pending
    except asyncio.CancelledError:
        # Native thread cancellation cannot stop a storage write. Join the
        # bounded operation before acknowledging cancellation to Temporal.
        with suppress(Exception):
            await asyncio.shield(pending)
        raise
