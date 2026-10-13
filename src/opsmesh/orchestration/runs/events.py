from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from opsmesh.agents.execution.contracts import AgentRunRequest
from opsmesh.agents.execution.errors import normalize_agent_error
from opsmesh.agents.messages.models import AgentMessage
from opsmesh.orchestration.runs.models import AgentRun, RunEvent
from opsmesh.orchestration.tasks.models import Task
from opsmesh.runtime.temporal.contracts import ExecutionRequest
from opsmesh.shared.security.redaction import redact_sensitive_payload, redact_sensitive_text
from opsmesh.shared.telemetry.request_context import current_evidence_context
from opsmesh.shared.telemetry.trace_context import with_current_trace_metadata
from opsmesh.teams.management.models import AgentTeam
from opsmesh.teams.sessions.service import TeamOperationService


class RunEventWriter:
    """Workspace-scoped, sequence-safe writer for durable run evidence."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def append(
        self,
        *,
        workspace_id: UUID,
        run_id: UUID,
        event_type: str,
        message: str,
        metadata: dict[str, object] | None = None,
        event_id: UUID | None = None,
    ) -> RunEvent:
        with self._session.no_autoflush:
            run = self._session.scalar(
                select(AgentRun)
                .where(AgentRun.id == run_id, AgentRun.workspace_id == workspace_id)
                .with_for_update()
            )
        if run is None:
            raise ValueError("Run event target not found in workspace")
        self._session.flush()
        sequence = (
            self._session.scalar(
                select(func.max(RunEvent.sequence)).where(
                    RunEvent.workspace_id == workspace_id,
                    RunEvent.agent_run_id == run_id,
                )
            )
            or 0
        ) + 1
        evidence = current_evidence_context()
        event = RunEvent(
            workspace_id=workspace_id,
            agent_run_id=run_id,
            sequence=sequence,
            event_type=event_type,
            message=redact_sensitive_text(message),
            request_id=evidence.get("request_id"),
            trace_id=evidence.get("trace_id"),
            span_id=evidence.get("span_id"),
            worker_id=evidence.get("worker_id"),
            runtime_id=evidence.get("runtime_id"),
            event_metadata=redact_sensitive_payload(with_current_trace_metadata(metadata)),
            created_at=datetime.now(UTC),
        )
        if event_id is not None:
            event.id = event_id
        self._session.add(event)
        self._session.flush([event])
        if event_type in {"run.completed", "run.failed", "run.cancelled"} and run.task_id:
            from opsmesh.orchestration.automations.dispatch import stage_automation_task_progress
            from opsmesh.orchestration.tasks.models import Task
            from opsmesh.teams.execution.dispatch import stage_team_task_progress

            task = self._session.scalar(
                select(Task).where(
                    Task.workspace_id == workspace_id,
                    Task.id == run.task_id,
                )
            )
            if task is not None:
                stage_team_task_progress(self._session, task=task, event_id=event.id)
                stage_automation_task_progress(
                    self._session,
                    workspace_id=workspace_id,
                    task_id=task.id,
                    event_id=event.id,
                )
        return event


@dataclass(slots=True)
class RunEventRecorder:
    session: Session

    def append_event(
        self,
        run: AgentRun,
        event_type: str,
        message: str,
        metadata: dict[str, object] | None = None,
    ) -> RunEvent:
        return RunEventWriter(self.session).append(
            workspace_id=run.workspace_id,
            run_id=run.id,
            event_type=event_type,
            message=message,
            metadata=metadata,
        )

    def append_run_claimed_event(self, run: AgentRun, job: ExecutionRequest) -> None:
        self.append_event(
            run,
            "run.claimed",
            "Worker claimed the run",
            {
                "execution": {
                    "workflow_id": job.workflow_id,
                    "kind": job.kind.value,
                    "request_id": job.request_id,
                    "requested_by_user_id": str(job.requested_by_user_id)
                    if job.requested_by_user_id is not None
                    else None,
                    "requested_by_agent_run_id": str(job.requested_by_agent_run_id)
                    if job.requested_by_agent_run_id is not None
                    else None,
                }
            },
        )

    def append_context_built_event(
        self,
        run: AgentRun,
        request: AgentRunRequest,
    ) -> None:
        metadata = request.context.metadata
        self.append_event(
            run,
            "run.context_built",
            "Agent runtime context built",
            {
                "agent_profile_id": str(request.agent_profile.id)
                if request.agent_profile.id is not None
                else None,
                "agent_role": request.agent_profile.role,
                "task_id": str(request.context.task_id)
                if request.context.task_id is not None
                else None,
                "task_step_id": optional_string_from_metadata(metadata, "task_step_id"),
                "work_package_id": optional_string_from_metadata(metadata, "work_package_id"),
                "model": request.model,
                "model_provider_credential_id": str(request.model_provider_credential_id)
                if request.model_provider_credential_id is not None
                else None,
                "allowed_tool_count": len(request.context.allowed_tools),
                "agent_tool_count": len(request.agent_tools),
                "has_tool_executor": request.tool_executor is not None,
                "context_assembly": metadata.get("context_assembly", {}),
                "memory_retrieval": metadata.get("memory_retrieval", {}),
            },
        )

    def append_model_provider_used_event(
        self,
        run: AgentRun,
        request: AgentRunRequest,
    ) -> None:
        self.append_event(
            run,
            "model_provider.used",
            "Model provider handled the run",
            {
                "model_provider": {
                    "provider": request.provider,
                    "model": request.model,
                    "model_api": request.model_api,
                    "credential_id": str(request.model_provider_credential_id)
                    if request.model_provider_credential_id is not None
                    else None,
                }
            },
        )

    def append_model_provider_unavailable_event(
        self,
        run: AgentRun,
        exc: Exception,
    ) -> None:
        error = normalize_agent_error(exc)
        event = self.append_event(
            run,
            "model_provider.unavailable",
            "Model provider was unavailable before the run started",
            {"reason": error.as_dict()},
        )
        self.append_team_runtime_model_provider_event(run, event)

    def append_team_runtime_model_provider_event(
        self,
        run: AgentRun,
        event: RunEvent,
    ) -> None:
        if run.task_id is None:
            return
        task = self.session.get(Task, run.task_id)
        if task is None or task.workspace_id != run.workspace_id or task.agent_team_id is None:
            return
        runtime_state = TeamOperationService(self.session).get_or_initialize_state(
            workspace_id=run.workspace_id,
            team_id=task.agent_team_id,
            initialize=True,
        )
        if runtime_state is None or runtime_state.thread_id is None:
            return
        team = self.session.get(AgentTeam, task.agent_team_id)
        if team is None or team.workspace_id != run.workspace_id:
            return
        sender_agent_id = team.manager_agent_profile_id or run.agent_profile_id
        message = AgentMessage(
            workspace_id=run.workspace_id,
            thread_id=runtime_state.thread_id,
            task_id=task.id,
            agent_team_id=task.agent_team_id,
            sender_agent_profile_id=sender_agent_id,
            recipient_agent_profile_id=run.agent_profile_id or sender_agent_id,
            message_type=f"team.runtime.{event.event_type}",
            body=event.message,
            payload={
                "run_id": str(run.id),
                "run_event_id": str(event.id),
                "task_id": str(task.id),
                "task_step_id": str(run.task_step_id) if run.task_step_id is not None else None,
                "agent_profile_id": str(run.agent_profile_id)
                if run.agent_profile_id is not None
                else None,
                "event_metadata": dict(event.event_metadata or {}),
            },
            status="sent",
            created_at=event.created_at,
        )
        self.session.add(message)
        self.session.flush([message])


def optional_string_from_metadata(metadata: dict[str, object], key: str) -> str | None:
    value = metadata.get(key)
    return value if isinstance(value, str) else None
