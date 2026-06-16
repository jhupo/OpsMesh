from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.runtimes.models import WorkspaceRuntime
from backend.app.self_hosted.models import (
    RuntimeCredential,
    SelfHostedJobClaim,
    SelfHostedMcpJob,
    SelfHostedWorker,
)


@dataclass(frozen=True, slots=True)
class SelfHostedMachineRecords:
    workers: list[SelfHostedWorker]
    runtimes: dict[UUID, WorkspaceRuntime]
    credentials: dict[UUID, RuntimeCredential]
    active_claim_counts: dict[UUID, int]
    mcp_claim_counts: dict[UUID, int]
    queued_mcp_counts: dict[UUID, int]


class SelfHostedMachineRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_workspace_records(self, workspace_id: UUID) -> SelfHostedMachineRecords:
        workers = self._session.scalars(
            select(SelfHostedWorker)
            .where(SelfHostedWorker.workspace_id == workspace_id)
            .order_by(SelfHostedWorker.updated_at.desc(), SelfHostedWorker.name.asc())
        ).all()
        if not workers:
            return SelfHostedMachineRecords(
                workers=[],
                runtimes={},
                credentials={},
                active_claim_counts={},
                mcp_claim_counts={},
                queued_mcp_counts={},
            )

        runtime_ids = [worker.workspace_runtime_id for worker in workers]
        worker_ids = [worker.id for worker in workers]
        return SelfHostedMachineRecords(
            workers=workers,
            runtimes=self._workspace_runtimes(workspace_id, runtime_ids),
            credentials=self._runtime_credentials(workspace_id, runtime_ids),
            active_claim_counts=self._active_job_claims(workspace_id, worker_ids),
            mcp_claim_counts=self._claimed_mcp_jobs(workspace_id, worker_ids),
            queued_mcp_counts=self._queued_mcp_jobs(workspace_id, runtime_ids),
        )

    def _workspace_runtimes(
        self,
        workspace_id: UUID,
        runtime_ids: list[UUID],
    ) -> dict[UUID, WorkspaceRuntime]:
        return {
            runtime.id: runtime
            for runtime in self._session.scalars(
                select(WorkspaceRuntime).where(
                    WorkspaceRuntime.workspace_id == workspace_id,
                    WorkspaceRuntime.id.in_(runtime_ids),
                )
            ).all()
        }

    def _runtime_credentials(
        self,
        workspace_id: UUID,
        runtime_ids: list[UUID],
    ) -> dict[UUID, RuntimeCredential]:
        return {
            credential.workspace_runtime_id: credential
            for credential in self._session.scalars(
                select(RuntimeCredential)
                .where(
                    RuntimeCredential.workspace_id == workspace_id,
                    RuntimeCredential.workspace_runtime_id.in_(runtime_ids),
                )
                .order_by(RuntimeCredential.created_at.asc())
            ).all()
        }

    def _active_job_claims(
        self,
        workspace_id: UUID,
        worker_ids: list[UUID],
    ) -> dict[UUID, int]:
        rows = self._session.execute(
            select(SelfHostedJobClaim.worker_id, func.count())
            .where(
                SelfHostedJobClaim.workspace_id == workspace_id,
                SelfHostedJobClaim.worker_id.in_(worker_ids),
                SelfHostedJobClaim.status == "claimed",
            )
            .group_by(SelfHostedJobClaim.worker_id)
        ).all()
        return _counts_by_uuid(rows)

    def _claimed_mcp_jobs(
        self,
        workspace_id: UUID,
        worker_ids: list[UUID],
    ) -> dict[UUID, int]:
        rows = self._session.execute(
            select(SelfHostedMcpJob.worker_id, func.count())
            .where(
                SelfHostedMcpJob.workspace_id == workspace_id,
                SelfHostedMcpJob.worker_id.in_(worker_ids),
                SelfHostedMcpJob.status == "claimed",
            )
            .group_by(SelfHostedMcpJob.worker_id)
        ).all()
        return _counts_by_uuid(rows)

    def _queued_mcp_jobs(
        self,
        workspace_id: UUID,
        runtime_ids: list[UUID],
    ) -> dict[UUID, int]:
        rows = self._session.execute(
            select(SelfHostedMcpJob.workspace_runtime_id, func.count())
            .where(
                SelfHostedMcpJob.workspace_id == workspace_id,
                SelfHostedMcpJob.workspace_runtime_id.in_(runtime_ids),
                SelfHostedMcpJob.status == "queued",
            )
            .group_by(SelfHostedMcpJob.workspace_runtime_id)
        ).all()
        return _counts_by_uuid(rows)


def _counts_by_uuid(rows: list[tuple[UUID | None, int]]) -> dict[UUID, int]:
    return {key: int(count) for key, count in rows if key is not None}
