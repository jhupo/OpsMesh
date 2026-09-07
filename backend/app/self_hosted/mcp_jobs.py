from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.api.schemas.self_hosted import McpJobCompleteRequest
from backend.app.capabilities.models import McpServer
from backend.app.runs.models import AgentRun
from backend.app.runtimes.models import WorkspaceRuntime
from backend.app.self_hosted.events import SelfHostedEventRecorder
from backend.app.self_hosted.jobs import SelfHostedJobFinalizer
from backend.app.self_hosted.models import SelfHostedMcpJob
from backend.app.self_hosted.types import AuthenticatedWorker


class SelfHostedMcpJobService:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._events = SelfHostedEventRecorder(session)
        self._jobs = SelfHostedJobFinalizer(session, self._events)

    def create_mcp_job(
        self,
        *,
        workspace_id: UUID,
        runtime_id: UUID,
        agent_run_id: UUID,
        mcp_server_id: UUID,
        tool_name: str,
        request_payload: dict[str, object],
    ) -> SelfHostedMcpJob:
        run = self._session.get(AgentRun, agent_run_id)
        server = self._session.get(McpServer, mcp_server_id)
        runtime = self._session.get(WorkspaceRuntime, runtime_id)
        if (
            run is None
            or server is None
            or runtime is None
            or run.workspace_id != workspace_id
            or server.workspace_id != workspace_id
            or runtime.workspace_id != workspace_id
            or run.runtime_id != runtime_id
        ):
            raise ValueError("Self-hosted MCP job scope is invalid")
        job = SelfHostedMcpJob(
            workspace_id=workspace_id,
            workspace_runtime_id=runtime_id,
            agent_run_id=agent_run_id,
            mcp_server_id=mcp_server_id,
            tool_name=tool_name,
            request_payload=request_payload,
        )
        self._session.add(job)
        self._session.flush()
        self._events.append_run_event(
            run,
            "self_hosted.mcp_job_queued",
            tool_name,
            {"mcp_job_id": str(job.id), "mcp_server_id": str(mcp_server_id)},
        )
        self._session.commit()
        self._session.refresh(job)
        return job

    def complete_mcp_job(
        self,
        auth: AuthenticatedWorker,
        mcp_job_id: UUID,
        data: McpJobCompleteRequest,
    ) -> SelfHostedMcpJob:
        job = self._locked_mcp_job(auth, mcp_job_id)
        if job.status in {"completed", "failed"}:
            if (
                job.status != data.status
                or job.response_payload != data.response_payload
                or job.error_payload != data.error_payload
            ):
                raise ValueError("Self-hosted MCP job was already completed differently")
            return job
        if job.status != "claimed" or job.worker_id != auth.worker.id:
            raise ValueError("Self-hosted MCP job is not claimed by this worker")
        job.status = data.status
        job.response_payload = data.response_payload
        job.error_payload = data.error_payload
        job.completed_at = datetime.now(UTC)
        run = self._session.get(AgentRun, job.agent_run_id)
        if run is not None:
            self._jobs.record_mcp_job_completion_for_run(run, job)
            self._events.append_run_event(
                run,
                f"self_hosted.mcp_job_{data.status}",
                job.tool_name,
                {
                    "mcp_job_id": str(job.id),
                    "mcp_server_id": str(job.mcp_server_id),
                    "response_present": data.response_payload is not None,
                    "error_present": data.error_payload is not None,
                },
            )
        self._session.commit()
        self._session.refresh(job)
        return job

    def _locked_mcp_job(
        self,
        auth: AuthenticatedWorker,
        mcp_job_id: UUID,
    ) -> SelfHostedMcpJob:
        job = self._session.scalar(
            select(SelfHostedMcpJob)
            .where(
                SelfHostedMcpJob.id == mcp_job_id,
                SelfHostedMcpJob.workspace_id == auth.worker.workspace_id,
                SelfHostedMcpJob.workspace_runtime_id == auth.runtime.id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if job is None:
            raise ValueError("Self-hosted MCP job not found")
        return job
