"""Durable task ownership transfer and handoff package orchestration."""

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.agents.models import AgentProfile
from backend.app.files.artifact_models import Artifact
from backend.app.observability.audit_service import AuditService
from backend.app.memory.models import WorkspaceMemoryEntry
from backend.app.orchestration.runs import RunOrchestrationService
from backend.app.orchestration.policies.statuses import ACTIVE_RUN_STATUS_VALUES
from backend.app.planning.models import TaskPlanningAttempt
from backend.app.runs.models import AgentRun
from backend.app.security.redaction import redact_sensitive_payload, redact_text_fragments
from backend.app.tasks.message_append import TaskMessageAppendService
from backend.app.tasks.models import Task, TaskMessage, TaskStep, TaskTransfer
from backend.app.tasks.ownership import is_platform_owned_step, owner_version
from backend.app.teams.models import AgentTeam, AgentTeamMember
from backend.app.teams.snapshots import build_team_snapshot
from backend.app.workers.queue.redis_queue import RedisQueue

TRANSFER_PENDING = "pending"
TRANSFER_ACCEPTED = "accepted"
TRANSFER_REJECTED = "rejected"
TRANSFER_CANCELLED = "cancelled"
TRANSFER_TERMINAL_STATUSES = {TRANSFER_ACCEPTED, TRANSFER_REJECTED, TRANSFER_CANCELLED}
class TaskTransferError(ValueError):
    """Stable domain error exposed by task transfer endpoints."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class TaskTransferCommand:
    target_agent_profile_id: UUID
    source_agent_profile_id: UUID | None
    reason: str
    idempotency_key: str | None = None


@dataclass(frozen=True, slots=True)
class TaskTransferDecision:
    reason: str | None = None
    enqueue: bool = False


class TaskTransferService:
    """Own the transfer transaction, including the immutable handoff package."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def request_transfer(
        self,
        *,
        workspace_id: UUID,
        task_id: UUID,
        actor_user_id: UUID,
        command: TaskTransferCommand,
        queue: RedisQueue | None = None,
    ) -> TaskTransfer | None:
        task = self._lock_task(workspace_id, task_id)
        if task is None:
            return None
        if task.agent_team_id is None:
            raise TaskTransferError(
                "task_transfer_team_required",
                "Only team-backed tasks can transfer ownership",
            )
        if task.status in {"completed", "cancelled"}:
            raise TaskTransferError(
                "task_transfer_terminal_task",
                "Terminal tasks cannot transfer ownership",
            )

        idempotency_key = command.idempotency_key or f"task-transfer:{uuid4()}"
        existing = self._session.scalar(
            select(TaskTransfer).where(
                TaskTransfer.workspace_id == workspace_id,
                TaskTransfer.idempotency_key == idempotency_key,
            )
        )
        if existing is not None:
            if existing.task_id != task.id:
                raise TaskTransferError(
                    "task_transfer_idempotency_conflict",
                    "Idempotency key is already bound to another task transfer",
                )
            return existing

        source_id = self._resolve_owner(task, command.source_agent_profile_id)
        if command.target_agent_profile_id == source_id:
            raise TaskTransferError(
                "task_transfer_same_owner",
                "Target agent is already the task owner",
            )
        self._require_target(task, command.target_agent_profile_id)
        self._require_no_blocking_runs(task)
        pending = self._session.scalar(
            select(TaskTransfer.id).where(
                TaskTransfer.workspace_id == workspace_id,
                TaskTransfer.task_id == task.id,
                TaskTransfer.status == TRANSFER_PENDING,
            )
        )
        if pending is not None:
            raise TaskTransferError(
                "task_transfer_already_pending",
                "A task transfer is already awaiting a decision",
            )

        revision = int(
            self._session.scalar(
                select(func.coalesce(func.max(TaskTransfer.revision), 0)).where(
                    TaskTransfer.workspace_id == workspace_id,
                    TaskTransfer.task_id == task.id,
                )
            )
            or 0
        ) + 1
        package = self._build_handoff_package(task, source_id, command.target_agent_profile_id)
        transfer = TaskTransfer(
            workspace_id=workspace_id,
            task_id=task.id,
            source_agent_profile_id=source_id,
            target_agent_profile_id=command.target_agent_profile_id,
            requested_by_user_id=actor_user_id,
            idempotency_key=idempotency_key,
            status=TRANSFER_PENDING,
            revision=revision,
            source_owner_version=owner_version(task),
            reason=_bounded_text(redact_text_fragments(command.reason), 1_000),
            handoff_package=package,
        )
        self._session.add(transfer)
        self._session.flush([transfer])
        self._append_message(
            task,
            message_type="task.transfer.requested",
            body="Task ownership transfer requested.",
            payload={
                "transfer_id": str(transfer.id),
                "revision": transfer.revision,
                "source_agent_profile_id": str(source_id),
                "target_agent_profile_id": str(command.target_agent_profile_id),
                "source_owner_version": transfer.source_owner_version,
                "status": transfer.status,
            },
        )
        AuditService(self._session).record_user_action(
            workspace_id=workspace_id,
            user_id=actor_user_id,
            action="task.transfer.requested",
            target_type="task_transfer",
            target_id=transfer.id,
            metadata={
                "task_id": str(task.id),
                "revision": transfer.revision,
                "source_agent_profile_id": str(source_id),
                "target_agent_profile_id": str(command.target_agent_profile_id),
                "source_owner_version": transfer.source_owner_version,
            },
        )
        self._session.commit()
        self._session.refresh(transfer)
        return transfer

    def accept_transfer(
        self,
        *,
        workspace_id: UUID,
        task_id: UUID,
        transfer_id: UUID,
        actor_user_id: UUID,
        decision: TaskTransferDecision,
        queue: RedisQueue | None = None,
    ) -> TaskTransfer | None:
        task = self._lock_task(workspace_id, task_id)
        if task is None:
            return None
        transfer = self._lock_transfer(workspace_id, task_id, transfer_id)
        if transfer is None:
            return None
        if transfer.status == TRANSFER_ACCEPTED:
            return transfer
        if transfer.status != TRANSFER_PENDING:
            raise TaskTransferError(
                "task_transfer_not_pending",
                "Only a pending task transfer can be accepted",
            )
        if transfer.target_agent_profile_id is None:
            raise TaskTransferError("task_transfer_target_missing", "Transfer target is missing")
        current_owner = self._resolve_owner(task, None)
        if current_owner != transfer.source_agent_profile_id:
            raise TaskTransferError(
                "task_transfer_source_changed",
                "Task ownership changed after this transfer was requested",
            )
        if owner_version(task) != transfer.source_owner_version:
            raise TaskTransferError(
                "task_transfer_owner_version_changed",
                "Task ownership version changed after this transfer was requested",
            )
        self._require_target(task, transfer.target_agent_profile_id)
        self._require_no_blocking_runs(task)

        previous_owner = current_owner
        next_version = owner_version(task) + 1
        task.owner_agent_profile_id = transfer.target_agent_profile_id
        task.owner_version = next_version
        self._reassign_platform_work(task, previous_owner, transfer.target_agent_profile_id)
        transfer.status = TRANSFER_ACCEPTED
        transfer.accepted_by_user_id = actor_user_id
        transfer.accepted_at = datetime.now(UTC)
        transfer.target_owner_version = next_version
        if decision.reason:
            transfer.reason = (
                f"{transfer.reason}\nAcceptance: "
                f"{redact_text_fragments(decision.reason)}"
            )
            transfer.reason = _bounded_text(transfer.reason, 1_000)
        self._append_message(
            task,
            message_type="task.transfer.accepted",
            body="Task ownership transfer accepted.",
            payload={
                "transfer_id": str(transfer.id),
                "revision": transfer.revision,
                "previous_owner_agent_profile_id": str(previous_owner)
                if previous_owner is not None
                else None,
                "owner_agent_profile_id": str(task.owner_agent_profile_id),
                "owner_version": task.owner_version,
            },
        )
        AuditService(self._session).record_user_action(
            workspace_id=workspace_id,
            user_id=actor_user_id,
            action="task.transfer.accepted",
            target_type="task_transfer",
            target_id=transfer.id,
            metadata={
                "task_id": str(task.id),
                "previous_owner_agent_profile_id": str(previous_owner)
                if previous_owner is not None
                else None,
                "owner_agent_profile_id": str(task.owner_agent_profile_id),
                "owner_version": task.owner_version,
            },
        )
        self._session.flush([task, transfer])
        if decision.enqueue and queue is not None:
            self._enqueue_next_run(task, actor_user_id, queue)
        self._session.commit()
        self._session.refresh(transfer)
        return transfer

    def reject_transfer(
        self,
        *,
        workspace_id: UUID,
        task_id: UUID,
        transfer_id: UUID,
        actor_user_id: UUID,
        decision: TaskTransferDecision,
    ) -> TaskTransfer | None:
        task = self._lock_task(workspace_id, task_id)
        if task is None:
            return None
        transfer = self._lock_transfer(workspace_id, task_id, transfer_id)
        if transfer is None:
            return None
        if transfer.status == TRANSFER_REJECTED:
            return transfer
        if transfer.status != TRANSFER_PENDING:
            raise TaskTransferError(
                "task_transfer_not_pending",
                "Only a pending task transfer can be rejected",
            )
        self._resolve_owner(task, None)
        if owner_version(task) != transfer.source_owner_version:
            raise TaskTransferError(
                "task_transfer_owner_version_changed",
                "Task ownership version changed after this transfer was requested",
            )
        transfer.status = TRANSFER_REJECTED
        transfer.accepted_by_user_id = actor_user_id
        transfer.rejected_at = datetime.now(UTC)
        transfer.rejection_reason = redact_text_fragments(
            decision.reason or "Target declined the ownership transfer."
        )
        transfer.rejection_reason = _bounded_text(transfer.rejection_reason, 1_000)
        self._append_message(
            task,
            message_type="task.transfer.rejected",
            body="Task ownership transfer rejected.",
            payload={
                "transfer_id": str(transfer.id),
                "revision": transfer.revision,
                "rejection_reason": transfer.rejection_reason,
            },
        )
        AuditService(self._session).record_user_action(
            workspace_id=workspace_id,
            user_id=actor_user_id,
            action="task.transfer.rejected",
            target_type="task_transfer",
            target_id=transfer.id,
            metadata={"task_id": str(task.id), "revision": transfer.revision},
        )
        self._session.commit()
        self._session.refresh(transfer)
        return transfer

    def list_transfers(
        self,
        *,
        workspace_id: UUID,
        task_id: UUID,
    ) -> list[TaskTransfer] | None:
        if self._session.scalar(
            select(Task.id).where(Task.workspace_id == workspace_id, Task.id == task_id)
        ) is None:
            return None
        return list(
            self._session.scalars(
                select(TaskTransfer)
                .where(
                    TaskTransfer.workspace_id == workspace_id,
                    TaskTransfer.task_id == task_id,
                )
                .order_by(TaskTransfer.revision.desc())
            ).all()
        )

    def _lock_task(self, workspace_id: UUID, task_id: UUID) -> Task | None:
        return self._session.scalar(
            select(Task)
            .where(Task.workspace_id == workspace_id, Task.id == task_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    def _lock_transfer(
        self,
        workspace_id: UUID,
        task_id: UUID,
        transfer_id: UUID,
    ) -> TaskTransfer | None:
        return self._session.scalar(
            select(TaskTransfer)
            .where(
                TaskTransfer.workspace_id == workspace_id,
                TaskTransfer.task_id == task_id,
                TaskTransfer.id == transfer_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    def _resolve_owner(self, task: Task, requested_source: UUID | None) -> UUID:
        owner = task.owner_agent_profile_id
        if owner is None:
            team = self._session.scalar(
                select(AgentTeam).where(
                    AgentTeam.workspace_id == task.workspace_id,
                    AgentTeam.id == task.agent_team_id,
                )
            )
            owner = team.manager_agent_profile_id if team is not None else None
            if owner is None:
                raise TaskTransferError(
                    "task_transfer_owner_missing",
                    "Team task has no current owner",
                )
            task.owner_agent_profile_id = owner
            task.owner_version = owner_version(task)
        if requested_source is not None and requested_source != owner:
            raise TaskTransferError(
                "task_transfer_source_mismatch",
                "Source agent is not the current task owner",
            )
        return owner

    def _require_target(self, task: Task, target_id: UUID) -> AgentProfile:
        profile = self._session.scalar(
            select(AgentProfile).where(
                AgentProfile.workspace_id == task.workspace_id,
                AgentProfile.id == target_id,
            )
        )
        if profile is None:
            raise TaskTransferError(
                "task_transfer_target_not_found",
                "Target agent does not belong to this workspace",
            )
        if profile.status != "active":
            raise TaskTransferError(
                "task_transfer_target_inactive",
                "Target agent is not active",
            )
        member = self._session.scalar(
            select(AgentTeamMember).where(
                AgentTeamMember.workspace_id == task.workspace_id,
                AgentTeamMember.agent_team_id == task.agent_team_id,
                AgentTeamMember.agent_profile_id == target_id,
            )
        )
        team = self._session.scalar(
            select(AgentTeam).where(
                AgentTeam.workspace_id == task.workspace_id,
                AgentTeam.id == task.agent_team_id,
            )
        )
        if team is None:
            raise TaskTransferError("task_transfer_team_not_found", "Task team was not found")
        if target_id == team.manager_agent_profile_id:
            return profile
        if member is None or member.status != "active" or not member.accepts_tasks:
            raise TaskTransferError(
                "task_transfer_target_not_available",
                "Target agent is not an active member accepting team tasks",
            )
        return profile

    def _require_no_blocking_runs(self, task: Task) -> None:
        run = self._session.scalar(
            select(AgentRun).where(
                AgentRun.workspace_id == task.workspace_id,
                AgentRun.task_id == task.id,
                AgentRun.status.in_(ACTIVE_RUN_STATUS_VALUES),
            )
        )
        if run is not None:
            raise TaskTransferError(
                "task_transfer_active_run",
                "Stop active task execution before transferring ownership",
            )

    def _build_handoff_package(
        self,
        task: Task,
        source_id: UUID,
        target_id: UUID,
    ) -> dict[str, object]:
        runs = list(
            self._session.scalars(
                select(AgentRun)
                .where(
                    AgentRun.workspace_id == task.workspace_id,
                    AgentRun.task_id == task.id,
                )
                .order_by(AgentRun.created_at.desc())
                .limit(20)
            ).all()
        )
        steps = list(
            self._session.scalars(
                select(TaskStep)
                .where(
                    TaskStep.workspace_id == task.workspace_id,
                    TaskStep.task_id == task.id,
                )
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
                .where(
                    Artifact.workspace_id == task.workspace_id,
                    Artifact.task_id == task.id,
                )
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
        return {
            "package_version": 1,
            "captured_at": datetime.now(UTC).isoformat(),
            "source_agent_profile_id": str(source_id),
            "target_agent_profile_id": str(target_id),
            "objective": {
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
            },
            "task_state": {
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
            },
            "steps": [_step_package(step) for step in steps],
            "messages": [
                {
                    "id": str(message.id),
                    "sequence": message.sequence,
                    "message_type": message.message_type,
                    "body": redact_text_fragments(message.body),
                    "payload": redact_sensitive_payload(
                        message.payload or {}, text_mode="fragments"
                    ),
                    "task_step_id": str(message.task_step_id)
                    if message.task_step_id is not None
                    else None,
                    "agent_run_id": str(message.agent_run_id)
                    if message.agent_run_id is not None
                    else None,
                }
                for message in messages
            ],
            "runs": [_run_package(run) for run in runs],
            "artifacts": [_artifact_package(artifact) for artifact in artifacts],
            "memory_references": [_memory_package(memory) for memory in memories],
        }

    def _reassign_platform_work(
        self,
        task: Task,
        source_id: UUID,
        target_id: UUID,
    ) -> None:
        for step in self._session.scalars(
            select(TaskStep).where(
                TaskStep.workspace_id == task.workspace_id,
                TaskStep.task_id == task.id,
                TaskStep.assigned_agent_profile_id == source_id,
            )
        ).all():
            if is_platform_owned_step(step) and step.status in {"queued", "blocked"}:
                step.assigned_agent_profile_id = target_id

        plan = task.project_plan if isinstance(task.project_plan, dict) else None
        if plan is not None:
            updated_plan = dict(plan)
            if updated_plan.get("planner_agent_profile_id") == str(source_id):
                updated_plan["planner_agent_profile_id"] = str(target_id)
            packages = updated_plan.get("work_packages")
            if isinstance(packages, list):
                updated_packages: list[object] = []
                for package in packages:
                    if not isinstance(package, dict):
                        updated_packages.append(package)
                        continue
                    updated_package = dict(package)
                    package_id = str(updated_package.get("package_id") or "")
                    raw_review_policy = updated_package.get("review_policy")
                    review_policy: dict[str, object] = (
                        dict(raw_review_policy) if isinstance(raw_review_policy, dict) else {}
                    )
                    if (
                        updated_package.get("assigned_agent_profile_id") == str(source_id)
                        and (
                            package_id in {"manager-planning", "manager-summary"}
                            or package_id.startswith("manager-summary-revision-")
                            or review_policy.get("mode") in {"final_acceptance", "executive_review"}
                        )
                    ):
                        updated_package["assigned_agent_profile_id"] = str(target_id)
                    updated_packages.append(updated_package)
                updated_plan["work_packages"] = updated_packages
            task.project_plan = updated_plan

        for attempt in self._session.scalars(
            select(TaskPlanningAttempt).where(
                TaskPlanningAttempt.workspace_id == task.workspace_id,
                TaskPlanningAttempt.task_id == task.id,
                TaskPlanningAttempt.planner_agent_profile_id == source_id,
                TaskPlanningAttempt.status.in_(("queued", "running")),
            )
        ).all():
            attempt.planner_agent_profile_id = target_id

        if task.team_snapshot is None and task.agent_team_id is not None:
            task.team_snapshot = build_team_snapshot(
                self._session,
                workspace_id=task.workspace_id,
                team_id=task.agent_team_id,
            )

    def _append_message(
        self,
        task: Task,
        *,
        message_type: str,
        body: str,
        payload: dict[str, object],
    ) -> TaskMessage:
        return TaskMessageAppendService(self._session).append_for_task(
            task,
            message_type=message_type,
            body=body,
            payload=payload,
        )

    def _enqueue_next_run(self, task: Task, actor_user_id: UUID, queue: RedisQueue) -> None:
        orchestration = RunOrchestrationService(self._session, queue=queue)
        run = orchestration.create_queued_run_for_task(task)
        if run is not None:
            orchestration.enqueue_run(run, actor_user_id)


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


def _bounded_text(value: str, limit: int) -> str:
    return value if len(value) <= limit else f"{value[: limit - 1]}…"
