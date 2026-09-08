from dataclasses import dataclass

from sqlalchemy.orm import Session

from backend.app.agent_messages.models import AgentMessage
from backend.app.agent_runtime.contracts import AgentRunRequest, AgentRunResult
from backend.app.agent_runtime.errors import AgentRuntimePolicyError, normalize_agent_error
from backend.app.orchestration.model_request_reviewing import model_provider_request_snapshot
from backend.app.orchestration.run_request_utils import dict_copy, json_safe
from backend.app.runs.event_writer import RunEventWriter
from backend.app.runs.models import AgentRun, RunEvent
from backend.app.tasks.models import Task
from backend.app.teams.models import AgentTeam
from backend.app.teams.runtime import TeamRuntimeService
from backend.app.workers.jobs import JobPayload


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
            workspace_id=run.workspace_id, run_id=run.id,
            event_type=event_type, message=message, metadata=metadata,
        )

    def append_run_claimed_event(self, run: AgentRun, job: JobPayload) -> None:
        self.append_event(
            run,
            "run.claimed",
            "Worker claimed the run",
            {
                "job": {
                    "job_id": str(job.job_id),
                    "attempt": job.attempt,
                    "priority": job.priority,
                    "created_at": job.created_at.isoformat(),
                    "requested_by_user_id": str(job.requested_by_user_id)
                    if job.requested_by_user_id is not None
                    else None,
                    "requested_by_agent_run_id": str(job.requested_by_agent_run_id)
                    if job.requested_by_agent_run_id is not None
                    else None,
                    "routing": safe_routing_metadata(job.routing),
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
                "continuation_count": len(request.continuations),
                "has_tool_executor": request.tool_executor is not None,
            },
        )

    def append_model_request_started_event(
        self,
        run: AgentRun,
        request: AgentRunRequest,
        *,
        fallback_selected: bool,
    ) -> None:
        self.append_event(
            run,
            "model.request_started",
            "Model request started",
            {
                "model": request.model,
                "model_provider_credential_id": str(request.model_provider_credential_id)
                if request.model_provider_credential_id is not None
                else None,
                "fallback_selected": fallback_selected,
                "allowed_tool_count": len(request.context.allowed_tools),
                "agent_tool_count": len(request.agent_tools),
                "continuation_count": len(request.continuations),
            },
        )

    def append_model_response_received_event(
        self,
        run: AgentRun,
        request: AgentRunRequest,
        result: AgentRunResult,
    ) -> None:
        self.append_event(
            run,
            "model.response_received",
            "Model response received",
            {
                "model": request.model,
                "model_provider_credential_id": str(request.model_provider_credential_id)
                if request.model_provider_credential_id is not None
                else None,
                "runtime_event_count": len(result.events),
                "guardrail_result_count": len(result.guardrail_results),
                "has_structured_output": result.structured_output is not None,
                "has_raw_output": result.raw_output is not None,
                "final_output_length": len(result.final_output),
            },
        )

    def append_model_request_failed_event(
        self,
        run: AgentRun,
        request: AgentRunRequest,
        exc: Exception,
    ) -> None:
        error = normalize_agent_error(exc)
        if isinstance(exc, AgentRuntimePolicyError):
            self.append_event(
                run,
                exc.event_type,
                exc.message,
                exc.metadata,
            )
        self.append_event(
            run,
            "model.request_failed",
            "Model request failed",
            {
                "model": request.model,
                "model_provider_credential_id": str(request.model_provider_credential_id)
                if request.model_provider_credential_id is not None
                else None,
                "reason": error.as_dict(),
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

    def append_model_provider_fallback_selected_event(
        self,
        run: AgentRun,
        *,
        failed_request: AgentRunRequest,
        selected_request: AgentRunRequest,
    ) -> None:
        event = self.append_event(
            run,
            "model_provider.fallback_selected",
            "Model provider fallback selected",
            {
                "failed_provider": model_provider_request_snapshot(failed_request),
                "model_provider": model_provider_request_snapshot(selected_request)
                | {
                    "source": "workspace_model_provider_fallback",
                    "selected_model": selected_request.model,
                },
            },
        )
        self.append_team_runtime_model_provider_event(run, event)

    def append_model_provider_fallback_unavailable_event(
        self,
        run: AgentRun,
        *,
        failed_request: AgentRunRequest,
        exc: Exception,
    ) -> None:
        error = normalize_agent_error(exc)
        event = self.append_event(
            run,
            "model_provider.fallback_unavailable",
            "Model provider fallback was unavailable",
            {
                "reason": error.as_dict(),
                "failed_provider": model_provider_request_snapshot(failed_request),
            },
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
        runtime_state = TeamRuntimeService(self.session).get_state(
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


def safe_routing_metadata(value: object) -> dict[str, object]:
    routing = dict_copy(value)
    return {
        key: json_safe(item)
        for key, item in routing.items()
        if key
        not in {
            "api_key",
            "authorization",
            "base_url",
            "container_id",
            "headers",
            "secret",
            "token",
        }
    }
