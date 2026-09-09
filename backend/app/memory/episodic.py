from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.agent_runtime.contracts import AgentRunResult
from backend.app.agents.memory_policy import EpisodicMemoryPolicy, episodic_memory_policy
from backend.app.agents.models import AgentProfile
from backend.app.approvals.models import Approval
from backend.app.memory.configuration import initial_embedding_status
from backend.app.memory.content import memory_content_fingerprint
from backend.app.memory.models import WorkspaceMemoryEntry
from backend.app.runs.models import AgentRun
from backend.app.security.redaction import redact_text_fragments
from backend.app.tasks.models import Task, TaskMessage

RUN_COMPLETED_ENTRY_TYPE = "agent_run_completed"
RUN_FAILED_ENTRY_TYPE = "agent_run_failed"
RUN_CANCELLED_ENTRY_TYPE = "agent_run_cancelled"
TASK_COMPLETED_ENTRY_TYPE = "task_completed"
APPROVAL_DECISION_ENTRY_TYPE = "approval_decision"


class AgentEpisodicMemoryService:
    """Records durable, redacted events with explicit task and run provenance."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def capture_run_completed(
        self,
        run: AgentRun,
        result: AgentRunResult,
        *,
        profile: AgentProfile | None = None,
        task: Task | None = None,
    ) -> WorkspaceMemoryEntry | None:
        profile = profile or self._profile(run.workspace_id, run.agent_profile_id)
        policy = self._policy(profile)
        if not policy.capture_enabled:
            return None
        task = task or self._task(run.workspace_id, run.task_id)
        content = redact_text_fragments(" ".join(result.final_output.split()))
        return self._create(
            workspace_id=run.workspace_id,
            policy=policy,
            memory_key=f"run:{run.id}:completed",
            entry_type=RUN_COMPLETED_ENTRY_TYPE,
            title=_run_title("Run completed", run, profile, task),
            content=content or "Agent run completed without textual output.",
            source_type="agent_run",
            source_id=str(run.id),
            task=task,
            run=run,
            profile=profile,
            tags=[
                "episode:run",
                "outcome:completed",
                *([f"agent_role:{profile.role}"] if profile is not None else []),
            ],
            metadata={"event": "run.completed"},
        )

    def capture_run_failed(
        self,
        run: AgentRun,
        error: dict[str, object],
        *,
        profile: AgentProfile | None = None,
        task: Task | None = None,
    ) -> WorkspaceMemoryEntry | None:
        profile = profile or self._profile(run.workspace_id, run.agent_profile_id)
        policy = self._policy(profile)
        if not policy.capture_enabled:
            return None
        task = task or self._task(run.workspace_id, run.task_id)
        code = redact_text_fragments(str(error.get("code") or "agent_run_failed"))
        message = redact_text_fragments(str(error.get("message") or "Agent run failed"))
        return self._create(
            workspace_id=run.workspace_id,
            policy=policy,
            memory_key=f"run:{run.id}:failed",
            entry_type=RUN_FAILED_ENTRY_TYPE,
            title=_run_title("Run failed", run, profile, task),
            content=f"{code}: {message}",
            source_type="agent_run",
            source_id=str(run.id),
            task=task,
            run=run,
            profile=profile,
            tags=["episode:run", "outcome:failed", f"error:{code}"],
            metadata={
                "event": "run.failed",
                "error": {"code": code, "retryable": bool(error.get("retryable"))},
            },
        )

    def capture_run_cancelled(
        self,
        run: AgentRun,
        *,
        profile: AgentProfile | None = None,
        task: Task | None = None,
    ) -> WorkspaceMemoryEntry | None:
        profile = profile or self._profile(run.workspace_id, run.agent_profile_id)
        policy = self._policy(profile)
        if not policy.capture_enabled:
            return None
        task = task or self._task(run.workspace_id, run.task_id)
        return self._create(
            workspace_id=run.workspace_id,
            policy=policy,
            memory_key=f"run:{run.id}:cancelled",
            entry_type=RUN_CANCELLED_ENTRY_TYPE,
            title=_run_title("Run cancelled", run, profile, task),
            content="Run was cancelled by a workspace user.",
            source_type="agent_run",
            source_id=str(run.id),
            task=task,
            run=run,
            profile=profile,
            tags=["episode:run", "outcome:cancelled"],
            metadata={"event": "run.cancelled"},
        )

    def capture_task_completed(
        self,
        task: Task,
        *,
        event_id: UUID,
        run: AgentRun | None = None,
        profile: AgentProfile | None = None,
        summary: str | None = None,
    ) -> WorkspaceMemoryEntry | None:
        profile = profile or self._profile(
            task.workspace_id,
            run.agent_profile_id if run is not None else None,
        )
        policy = self._policy(profile)
        if not policy.capture_enabled:
            return None
        content = redact_text_fragments(summary or task.description or task.title)
        return self._create(
            workspace_id=task.workspace_id,
            policy=policy,
            memory_key=f"task:{task.id}:completed:{event_id}",
            entry_type=TASK_COMPLETED_ENTRY_TYPE,
            title=f"Task completed: {redact_text_fragments(task.title)}"[:240],
            content=content or "Task completed.",
            source_type="task",
            source_id=str(task.id),
            task=task,
            run=run,
            profile=profile,
            tags=["episode:task", "outcome:completed"],
            metadata={"event": "task.completed", "event_id": str(event_id)},
        )

    def capture_task_message(self, message: TaskMessage) -> WorkspaceMemoryEntry | None:
        episode = _task_message_episode(message.message_type)
        if episode is None:
            return None
        profile = self._profile(message.workspace_id, message.agent_profile_id)
        policy = self._policy(profile)
        if not policy.capture_enabled:
            return None
        task = self._task(message.workspace_id, message.task_id)
        if task is None:
            return None
        category, outcome = episode
        return self._create(
            workspace_id=message.workspace_id,
            policy=policy,
            memory_key=f"task-message:{message.id}",
            entry_type=f"task_{category}",
            title=(
                f"{category.replace('_', ' ').title()}: "
                f"{redact_text_fragments(task.title)}"
            )[:240],
            content=redact_text_fragments(message.body) or message.message_type,
            source_type="task_message",
            source_id=str(message.id),
            task=task,
            run=self._run(message.workspace_id, message.agent_run_id),
            profile=profile,
            created_by_user_id=_payload_user_id(message.payload),
            tags=[f"episode:{category}", f"outcome:{outcome}"],
            metadata={
                "event": message.message_type,
                "task_message": {
                    "id": str(message.id),
                    "sequence": message.sequence,
                    "message_type": message.message_type,
                    "task_step_id": str(message.task_step_id)
                    if message.task_step_id is not None
                    else None,
                },
            },
        )

    def capture_approval_decision(
        self,
        approval: Approval,
        *,
        actor_user_id: UUID,
    ) -> WorkspaceMemoryEntry | None:
        profile = self._profile(
            approval.workspace_id,
            approval.requested_by_agent_profile_id,
        )
        policy = self._policy(profile)
        if not policy.capture_enabled:
            return None
        task = self._task(approval.workspace_id, approval.task_id)
        run = self._run(approval.workspace_id, approval.agent_run_id)
        reason = redact_text_fragments(approval.decision_reason or "No reason provided")
        return self._create(
            workspace_id=approval.workspace_id,
            policy=policy,
            memory_key=f"approval:{approval.id}:{approval.status}",
            entry_type=APPROVAL_DECISION_ENTRY_TYPE,
            title=f"Approval {approval.status}: {approval.approval_type}"[:240],
            content=reason,
            source_type="approval",
            source_id=str(approval.id),
            task=task,
            run=run,
            profile=profile,
            created_by_user_id=actor_user_id,
            tags=["episode:decision", f"outcome:{approval.status}"],
            metadata={
                "event": f"approval.{approval.status}",
                "approval": {
                    "id": str(approval.id),
                    "type": approval.approval_type,
                    "risk_level": approval.risk_level,
                    "status": approval.status,
                },
            },
        )

    def _create(
        self,
        *,
        workspace_id: UUID,
        policy: EpisodicMemoryPolicy,
        memory_key: str,
        entry_type: str,
        title: str,
        content: str,
        source_type: str,
        source_id: str,
        task: Task | None,
        run: AgentRun | None,
        profile: AgentProfile | None,
        tags: list[str],
        metadata: dict[str, object],
        created_by_user_id: UUID | None = None,
    ) -> WorkspaceMemoryEntry:
        scope_type = "task" if task is not None else "run" if run is not None else "workspace"
        scope_id = (
            str(task.id)
            if task is not None
            else str(run.id)
            if run is not None
            else str(workspace_id)
        )
        existing = self._session.scalar(
            select(WorkspaceMemoryEntry).where(
                WorkspaceMemoryEntry.workspace_id == workspace_id,
                WorkspaceMemoryEntry.memory_layer == "episodic",
                WorkspaceMemoryEntry.scope_type == scope_type,
                WorkspaceMemoryEntry.scope_id == scope_id,
                WorkspaceMemoryEntry.memory_key == memory_key,
            )
        )
        if existing is not None:
            return existing
        captured_at = datetime.now(UTC)
        provenance: dict[str, object] = {
            "capture_version": 2,
            "captured_at": captured_at.isoformat(),
            **metadata,
        }
        if task is not None:
            provenance["task"] = {
                "id": str(task.id),
                "title": redact_text_fragments(task.title),
                "status": task.status,
                "domain_type": task.domain_type,
                "agent_team_id": str(task.agent_team_id)
                if task.agent_team_id is not None
                else None,
            }
        if run is not None:
            provenance["run"] = {
                "id": str(run.id),
                "status": run.status,
                "task_step_id": str(run.task_step_id)
                if run.task_step_id is not None
                else None,
                "completed_at": run.completed_at.isoformat() if run.completed_at else None,
            }
        if profile is not None:
            provenance["agent"] = {
                "profile_id": str(profile.id),
                "name": redact_text_fragments(profile.name),
                "role": redact_text_fragments(profile.role),
                "version": profile.version,
            }
        entry = WorkspaceMemoryEntry(
            workspace_id=workspace_id,
            created_by_user_id=created_by_user_id,
            created_by_agent_profile_id=profile.id if profile is not None else None,
            created_by_agent_run_id=run.id if run is not None else None,
            source_type=source_type,
            source_id=source_id,
            memory_layer="episodic",
            scope_type=scope_type,
            scope_id=scope_id,
            memory_key=memory_key,
            entry_type=entry_type,
            title=title,
            content=content,
            tags=tags,
            visibility_scope="workspace",
            importance=policy.default_importance,
            status="active",
            content_fingerprint=memory_content_fingerprint(title, content),
            memory_metadata=provenance,
            expires_at=captured_at + timedelta(days=policy.retention_days),
            embedding_status=initial_embedding_status(self._session, workspace_id),
        )
        self._session.add(entry)
        self._session.flush([entry])
        return entry

    def _profile(self, workspace_id: UUID, profile_id: UUID | None) -> AgentProfile | None:
        if profile_id is None:
            return None
        profile = self._session.get(AgentProfile, profile_id)
        if profile is None or profile.workspace_id != workspace_id:
            return None
        return profile

    def _task(self, workspace_id: UUID, task_id: UUID | None) -> Task | None:
        if task_id is None:
            return None
        task = self._session.get(Task, task_id)
        if task is None or task.workspace_id != workspace_id:
            return None
        return task

    def _run(self, workspace_id: UUID, run_id: UUID | None) -> AgentRun | None:
        if run_id is None:
            return None
        run = self._session.get(AgentRun, run_id)
        if run is None or run.workspace_id != workspace_id:
            return None
        return run

    @staticmethod
    def _policy(profile: AgentProfile | None) -> EpisodicMemoryPolicy:
        return episodic_memory_policy(profile.memory_policy if profile is not None else {})


def _run_title(
    prefix: str,
    run: AgentRun,
    profile: AgentProfile | None,
    task: Task | None,
) -> str:
    subject = (
        task.title
        if task is not None
        else profile.name or profile.role
        if profile is not None
        else str(run.id)
    )
    return f"{prefix}: {redact_text_fragments(subject)}"[:240]


def _task_message_episode(message_type: str) -> tuple[str, str] | None:
    if message_type in {
        "human.feedback",
        "task.correction.created",
        "task.control.add_instruction",
    }:
        return "human_feedback", "recorded"
    if message_type == "pm.acceptance_decision" or message_type.startswith("task.operator."):
        return "decision", "recorded"
    if message_type.endswith(".failed") or message_type.endswith(".blocked"):
        return "failure", "failed"
    return None


def _payload_user_id(payload: object) -> UUID | None:
    if not isinstance(payload, dict):
        return None
    raw_value = payload.get("actor_user_id")
    if not isinstance(raw_value, str):
        return None
    try:
        return UUID(raw_value)
    except ValueError:
        return None
