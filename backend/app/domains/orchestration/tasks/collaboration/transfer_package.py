"""Build the immutable, redacted context attached to a task transfer."""

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.security.redaction import redact_sensitive_payload, redact_text_fragments
from backend.app.domains.agents.memory.models import WorkspaceMemoryEntry
from backend.app.domains.orchestration.runs.models import AgentRun
from backend.app.domains.orchestration.tasks.control.ownership import owner_version
from backend.app.domains.orchestration.tasks.models import Task, TaskMessage, TaskStep
from backend.app.domains.workspace.storage.artifact_models import Artifact


@dataclass(frozen=True, slots=True)
class _TaskHandoffContext:
    runs: list[AgentRun]
    steps: list[TaskStep]
    messages: list[TaskMessage]
    artifacts: list[Artifact]
    memories: list[WorkspaceMemoryEntry]


class TaskHandoffPackageBuilder:
    """Read task execution context and produce a bounded transfer snapshot."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def build(self, task: Task, source_id: UUID, target_id: UUID) -> dict[str, object]:
        context = self._load_context(task)
        return {
            "package_version": 1,
            "captured_at": datetime.now(UTC).isoformat(),
            "source_agent_profile_id": str(source_id),
            "target_agent_profile_id": str(target_id),
            "objective": self._objective(task),
            "task_state": self._task_state(task),
            "steps": [_step_package(step) for step in context.steps],
            "messages": [_message_package(message) for message in context.messages],
            "runs": [_run_package(run) for run in context.runs],
            "artifacts": [_artifact_package(artifact) for artifact in context.artifacts],
            "memory_references": [_memory_package(memory) for memory in context.memories],
        }

    def _load_context(self, task: Task) -> _TaskHandoffContext:
        runs = list(
            self._session.scalars(
                select(AgentRun)
                .where(AgentRun.workspace_id == task.workspace_id, AgentRun.task_id == task.id)
                .order_by(AgentRun.created_at.desc())
                .limit(20)
            ).all()
        )
        steps = list(
            self._session.scalars(
                select(TaskStep)
                .where(TaskStep.workspace_id == task.workspace_id, TaskStep.task_id == task.id)
                .order_by(TaskStep.order_index.asc(), TaskStep.created_at.asc())
            ).all()
        )
        messages = list(
            self._session.scalars(
                select(TaskMessage)
                .where(
                    TaskMessage.workspace_id == task.workspace_id,
                    TaskMessage.task_id == task.id,
                )
                .order_by(TaskMessage.sequence.desc())
                .limit(50)
            ).all()
        )
        messages.reverse()
        artifacts = list(
            self._session.scalars(
                select(Artifact)
                .where(Artifact.workspace_id == task.workspace_id, Artifact.task_id == task.id)
                .order_by(Artifact.created_at.desc())
            ).all()
        )
        run_ids = [run.id for run in runs]
        memory_filters = [
            (WorkspaceMemoryEntry.scope_type == "task")
            & (WorkspaceMemoryEntry.scope_id == str(task.id)),
        ]
        if run_ids:
            memory_filters.append(
                (WorkspaceMemoryEntry.scope_type == "run")
                & WorkspaceMemoryEntry.scope_id.in_([str(run_id) for run_id in run_ids])
            )
        memories = list(
            self._session.scalars(
                select(WorkspaceMemoryEntry)
                .where(
                    WorkspaceMemoryEntry.workspace_id == task.workspace_id,
                    WorkspaceMemoryEntry.status == "active",
                    *memory_filters,
                )
                .order_by(WorkspaceMemoryEntry.updated_at.desc())
                .limit(100)
            ).all()
        )
        return _TaskHandoffContext(
            runs=runs,
            steps=steps,
            messages=messages,
            artifacts=artifacts,
            memories=memories,
        )

    @staticmethod
    def _objective(task: Task) -> dict[str, object]:
        return {
            "title": redact_text_fragments(task.title),
            "description": redact_text_fragments(task.description),
            "domain_type": task.domain_type,
            "priority": task.priority,
            "input": redact_sensitive_payload(task.input or {}, text_mode="fragments"),
            "generic_state": redact_sensitive_payload(
                task.generic_state or {}, text_mode="fragments"
            ),
            "domain_state": redact_sensitive_payload(
                task.domain_state or {}, text_mode="fragments"
            ),
        }

    @staticmethod
    def _task_state(task: Task) -> dict[str, object]:
        return {
            "status": task.status,
            "owner_agent_profile_id": str(task.owner_agent_profile_id)
            if task.owner_agent_profile_id is not None
            else None,
            "owner_version": owner_version(task),
            "project_plan": redact_sensitive_payload(
                task.project_plan or {}, text_mode="fragments"
            ),
            "final_output": redact_sensitive_payload(task.final_output, text_mode="fragments")
            if task.final_output is not None
            else None,
        }


def _step_package(step: TaskStep) -> dict[str, object]:
    return {
        "id": str(step.id),
        "work_package_id": step.work_package_id,
        "title": redact_text_fragments(step.title),
        "description": redact_text_fragments(step.description),
        "status": step.status,
        "order_index": step.order_index,
        "assigned_agent_profile_id": str(step.assigned_agent_profile_id)
        if step.assigned_agent_profile_id is not None
        else None,
        "required_role": step.required_role,
        "required_skills": list(step.required_skills or []),
        "expected_artifacts": list(step.expected_artifacts or []),
        "acceptance_criteria": [
            redact_text_fragments(item) for item in step.acceptance_criteria or []
        ],
        "review_policy": redact_sensitive_payload(step.review_policy or {}, text_mode="fragments"),
        "dependencies": redact_sensitive_payload(step.dependencies or {}, text_mode="fragments"),
        "result_summary": redact_text_fragments(step.result_summary)
        if step.result_summary is not None
        else None,
    }


def _message_package(message: TaskMessage) -> dict[str, object]:
    return {
        "id": str(message.id),
        "sequence": message.sequence,
        "message_type": message.message_type,
        "body": redact_text_fragments(message.body),
        "payload": redact_sensitive_payload(message.payload or {}, text_mode="fragments"),
        "task_step_id": str(message.task_step_id) if message.task_step_id is not None else None,
        "agent_run_id": str(message.agent_run_id) if message.agent_run_id is not None else None,
    }


def _run_package(run: AgentRun) -> dict[str, object]:
    return {
        "id": str(run.id),
        "task_step_id": str(run.task_step_id) if run.task_step_id is not None else None,
        "agent_profile_id": str(run.agent_profile_id) if run.agent_profile_id is not None else None,
        "status": run.status,
        "model": run.model,
        "input": redact_sensitive_payload(run.input or {}, text_mode="fragments"),
        "output": redact_sensitive_payload(run.output, text_mode="fragments")
        if run.output is not None
        else None,
        "error": redact_sensitive_payload(run.error, text_mode="fragments")
        if run.error is not None
        else None,
        "started_at": run.started_at.isoformat() if run.started_at is not None else None,
        "completed_at": run.completed_at.isoformat() if run.completed_at is not None else None,
    }


def _artifact_package(artifact: Artifact) -> dict[str, object]:
    return {
        "id": str(artifact.id),
        "task_step_id": str(artifact.task_step_id) if artifact.task_step_id is not None else None,
        "agent_run_id": str(artifact.agent_run_id) if artifact.agent_run_id is not None else None,
        "agent_profile_id": str(artifact.agent_profile_id)
        if artifact.agent_profile_id is not None
        else None,
        "work_package_id": artifact.work_package_id,
        "project_path": artifact.project_path,
        "version": artifact.version,
        "review_status": artifact.review_status,
        "artifact_type": artifact.artifact_type,
        "filename": redact_text_fragments(artifact.filename),
        "content_type": artifact.content_type,
        "size_bytes": artifact.size_bytes,
        "checksum_sha256": artifact.checksum_sha256,
        "metadata": redact_sensitive_payload(
            artifact.artifact_metadata or {}, text_mode="fragments"
        ),
    }


def _memory_package(memory: WorkspaceMemoryEntry) -> dict[str, object]:
    return {
        "id": str(memory.id),
        "memory_layer": memory.memory_layer,
        "scope_type": memory.scope_type,
        "scope_id": memory.scope_id,
        "entry_type": memory.entry_type,
        "title": redact_text_fragments(memory.title),
        "revision": memory.revision,
        "status": memory.status,
        "source_type": memory.source_type,
        "source_id": memory.source_id,
        "content_fingerprint": memory.content_fingerprint,
        "tags": list(memory.tags or []),
    }
