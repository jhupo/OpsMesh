"""Durable execution boundary for user-authored subworkflow nodes."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.orchestration.models import (
    OrchestrationDefinition,
    OrchestrationRevision,
    SubworkflowInvocation,
)
from backend.app.orchestration.run_events import RunEventRecorder
from backend.app.orchestration.runs import RunOrchestrationService
from backend.app.orchestration.workflow_data import resolve_workflow_inputs
from backend.app.runs.models import AgentRun
from backend.app.tasks.models import Task, TaskStep
from backend.app.tasks.workspace_service import WorkspaceTaskService
from backend.app.workers.queue.redis_queue import RedisQueue


class SubworkflowExecutionError(ValueError):
    """Raised when a subworkflow node cannot be admitted for execution."""


@dataclass(frozen=True, slots=True)
class SubworkflowLaunch:
    status: str
    invocation_id: UUID
    child_task_id: UUID
    child_run_id: UUID | None
    output: dict[str, object] | None = None
    error: dict[str, object] | None = None


class SubworkflowExecutionService:
    def __init__(self, session: Session, queue: RedisQueue | None = None) -> None:
        self._session = session
        self._queue = queue

    def launch(
        self,
        *,
        parent_run: AgentRun,
        parent_task: Task,
        parent_step: TaskStep,
        requested_by_user_id: UUID | None,
    ) -> SubworkflowLaunch:
        existing = self._session.scalar(
            select(SubworkflowInvocation)
            .where(
                SubworkflowInvocation.workspace_id == parent_run.workspace_id,
                SubworkflowInvocation.parent_run_id == parent_run.id,
            )
            .with_for_update()
        )
        if existing is not None:
            return self._launch_from_invocation(existing)

        dependencies = parent_step.dependencies
        if not isinstance(dependencies, dict):
            raise SubworkflowExecutionError("Subworkflow node dependencies are invalid")
        definition_id = self._uuid_value(dependencies.get("subworkflow_definition_id"))
        if definition_id is None:
            raise SubworkflowExecutionError("Subworkflow node has no definition")
        definition = self._session.scalar(
            select(OrchestrationDefinition).where(
                OrchestrationDefinition.workspace_id == parent_run.workspace_id,
                OrchestrationDefinition.id == definition_id,
            )
        )
        if definition is None or definition.status == "archived":
            raise SubworkflowExecutionError("Subworkflow definition is unavailable")
        requested_version = dependencies.get("subworkflow_version")
        version = (
            int(requested_version)
            if isinstance(requested_version, int)
            and not isinstance(requested_version, bool)
            and requested_version > 0
            else None
        )
        revision_query = select(OrchestrationRevision).where(
            OrchestrationRevision.workspace_id == parent_run.workspace_id,
            OrchestrationRevision.definition_id == definition.id,
        )
        if version is not None:
            revision_query = revision_query.where(OrchestrationRevision.version == version)
        revision = self._session.scalar(
            revision_query.order_by(OrchestrationRevision.version.desc()).limit(1)
        )
        if revision is None:
            raise SubworkflowExecutionError("Subworkflow definition has no published revision")

        arguments = dependencies.get("arguments", {})
        if not isinstance(arguments, dict):
            raise SubworkflowExecutionError("Subworkflow arguments must be an object")
        if any(not isinstance(key, str) for key in arguments):
            raise SubworkflowExecutionError("Subworkflow arguments must use string keys")
        arguments_payload: dict[str, object] = {
            key: value for key, value in arguments.items() if isinstance(key, str)
        }
        arguments_payload.update(resolve_workflow_inputs(self._session, parent_task, parent_step))
        child_input: dict[str, object] = {
            "parent_task_id": str(parent_task.id),
            "parent_task_step_id": str(parent_step.id),
            "parent_run_id": str(parent_run.id),
            "arguments": arguments_payload,
            "parent_input": parent_task.input,
        }
        self._assert_payload_bound(child_input)
        child_task, child_run = WorkspaceTaskService(self._session).create_subworkflow_task(
            parent_task=parent_task,
            parent_run_id=parent_run.id,
            title=f"{parent_task.title} / {parent_step.title}",
            description=parent_step.description,
            input_payload=child_input,
            orchestration_definition_id=definition.id,
            orchestration_version=revision.version,
            queue=self._queue,
        )
        invocation = SubworkflowInvocation(
            workspace_id=parent_run.workspace_id,
            parent_task_id=parent_task.id,
            parent_task_step_id=parent_step.id,
            parent_run_id=parent_run.id,
            child_task_id=child_task.id,
            definition_id=definition.id,
            definition_version=revision.version,
            status="running",
            input_payload=child_input,
            started_at=datetime.now(UTC),
        )
        self._session.add(invocation)
        self._session.flush([invocation])
        RunEventRecorder(self._session).append_event(
            parent_run,
            "subworkflow.started",
            "Subworkflow child task started",
            {
                "invocation_id": str(invocation.id),
                "child_task_id": str(child_task.id),
                "child_run_id": str(child_run.id) if child_run is not None else None,
                "definition_id": str(definition.id),
                "definition_version": revision.version,
            },
        )

        if child_run is None:
            RunOrchestrationService(self._session, queue=self._queue).schedule_workspace_steps(
                workspace_id=child_task.workspace_id,
                requested_by_user_id=requested_by_user_id,
            )
            self._session.refresh(child_task)
            if child_task.status != "completed":
                raise SubworkflowExecutionError(
                    "Subworkflow child task did not produce an executable run"
                )
            output = child_task.final_output or {}
            invocation.status = "completed"
            invocation.output_payload = output
            invocation.completed_at = datetime.now(UTC)
            self._session.flush([invocation])
            return SubworkflowLaunch(
                status="completed",
                invocation_id=invocation.id,
                child_task_id=child_task.id,
                child_run_id=None,
                output=output,
            )

        return SubworkflowLaunch(
            status="waiting_subworkflow",
            invocation_id=invocation.id,
            child_task_id=child_task.id,
            child_run_id=child_run.id,
        )

    def _launch_from_invocation(self, invocation: SubworkflowInvocation) -> SubworkflowLaunch:
        status = invocation.status
        return SubworkflowLaunch(
            status="waiting_subworkflow" if status == "running" else status,
            invocation_id=invocation.id,
            child_task_id=invocation.child_task_id,
            child_run_id=self._child_run_id(
                invocation.workspace_id,
                invocation.child_task_id,
            ),
            output=invocation.output_payload,
            error=invocation.error_payload,
        )

    def _child_run_id(self, workspace_id: UUID, child_task_id: UUID) -> UUID | None:
        return self._session.scalar(
            select(AgentRun.id)
            .where(
                AgentRun.workspace_id == workspace_id,
                AgentRun.task_id == child_task_id,
            )
            .order_by(AgentRun.created_at.asc())
            .limit(1)
        )

    @staticmethod
    def _uuid_value(value: object) -> UUID | None:
        if value is None:
            return None
        try:
            return value if isinstance(value, UUID) else UUID(str(value))
        except (TypeError, ValueError) as exc:
            raise SubworkflowExecutionError("Subworkflow definition ID is invalid") from exc

    @staticmethod
    def _assert_payload_bound(payload: dict[str, object]) -> None:
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)
        if len(encoded.encode("utf-8")) > 64 * 1024:
            raise SubworkflowExecutionError("Subworkflow input exceeds the 64 KiB limit")


__all__ = ["SubworkflowExecutionError", "SubworkflowExecutionService", "SubworkflowLaunch"]
