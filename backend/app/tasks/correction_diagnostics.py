from __future__ import annotations

from collections import Counter, defaultdict
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.artifacts.models import Artifact
from backend.app.core.typing import dict_or_empty, uuid_or_none
from backend.app.runs.models import AgentRun
from backend.app.runs.status import RunStatus
from backend.app.security.redaction import redact_sensitive_payload
from backend.app.tasks.models import Task, TaskMessage, TaskStep

ACTIVE_RUN_STATUSES = {
    RunStatus.QUEUED.value,
    RunStatus.RUNNING.value,
    RunStatus.WAITING_RUNTIME.value,
    RunStatus.WAITING_APPROVAL.value,
    RunStatus.WAITING_SUBWORKFLOW.value,
}


class TaskCorrectionDiagnosticsService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_diagnostics(
        self,
        *,
        workspace_id: UUID,
        task_id: UUID,
    ) -> dict[str, object] | None:
        task = self._session.scalar(
            select(Task).where(Task.workspace_id == workspace_id, Task.id == task_id)
        )
        if task is None:
            return None

        messages = self._correction_messages(workspace_id, task.id)
        steps = self._steps(workspace_id, task.id)
        step_by_id = {step.id: step for step in steps}
        runs_by_step_id = self._runs_by_step_id(workspace_id, task.id)
        artifacts_by_step_id = self._artifacts_by_step_id(workspace_id, task.id)
        corrections = [
            self._correction_payload(
                task,
                message,
                step_by_id=step_by_id,
                runs_by_step_id=runs_by_step_id,
                artifacts_by_step_id=artifacts_by_step_id,
            )
            for message in messages
        ]
        return {
            "workspace_id": workspace_id,
            "task_id": task.id,
            "generated_at": datetime.now(UTC),
            "summary": _summary(corrections),
            "corrections": corrections,
        }

    def _correction_payload(
        self,
        task: Task,
        message: TaskMessage,
        *,
        step_by_id: dict[UUID, TaskStep],
        runs_by_step_id: dict[UUID, list[AgentRun]],
        artifacts_by_step_id: dict[UUID, list[Artifact]],
    ) -> dict[str, object]:
        payload = message.payload if isinstance(message.payload, dict) else {}
        created_step_id = uuid_or_none(payload.get("created_step_id"))
        step = step_by_id.get(created_step_id) if created_step_id is not None else None
        runs = runs_by_step_id.get(step.id, []) if step is not None else []
        artifacts = artifacts_by_step_id.get(step.id, []) if step is not None else []
        mode = str(payload.get("mode") or "")
        target = dict_or_empty(payload.get("target"))
        metadata = dict_or_empty(payload.get("metadata"))
        blocked_reasons = _blocked_reasons(
            task=task,
            mode=mode,
            target=target,
            created_step_id=created_step_id,
            step=step,
            runs=runs,
            artifacts=artifacts,
        )
        return {
            "message_id": message.id,
            "sequence": message.sequence,
            "mode": mode,
            "target_type": payload.get("target_type") if isinstance(
                payload.get("target_type"),
                str,
            ) else target.get("target_type"),
            "target": redact_sensitive_payload(target),
            "instruction": message.body,
            "metadata": redact_sensitive_payload(metadata),
            "actor_user_id": uuid_or_none(payload.get("actor_user_id")),
            "created_step": _step_payload(step),
            "runs": [_run_payload(run) for run in runs],
            "artifacts": [_artifact_payload(artifact) for artifact in artifacts],
            "status": _correction_status(task, mode, step, runs),
            "blocked_reasons": blocked_reasons,
            "created_at": message.created_at,
        }

    def _correction_messages(self, workspace_id: UUID, task_id: UUID) -> list[TaskMessage]:
        return list(
            self._session.scalars(
                select(TaskMessage)
                .where(
                    TaskMessage.workspace_id == workspace_id,
                    TaskMessage.task_id == task_id,
                    TaskMessage.message_type == "task.correction.created",
                )
                .order_by(TaskMessage.sequence.asc())
            )
        )

    def _steps(self, workspace_id: UUID, task_id: UUID) -> list[TaskStep]:
        return list(
            self._session.scalars(
                select(TaskStep).where(
                    TaskStep.workspace_id == workspace_id,
                    TaskStep.task_id == task_id,
                )
            )
        )

    def _runs_by_step_id(
        self,
        workspace_id: UUID,
        task_id: UUID,
    ) -> dict[UUID, list[AgentRun]]:
        runs = self._session.scalars(
            select(AgentRun)
            .where(
                AgentRun.workspace_id == workspace_id,
                AgentRun.task_id == task_id,
                AgentRun.task_step_id.is_not(None),
            )
            .order_by(AgentRun.created_at.asc())
        ).all()
        by_step: dict[UUID, list[AgentRun]] = defaultdict(list)
        for run in runs:
            if run.task_step_id is not None:
                by_step[run.task_step_id].append(run)
        return by_step

    def _artifacts_by_step_id(
        self,
        workspace_id: UUID,
        task_id: UUID,
    ) -> dict[UUID, list[Artifact]]:
        artifacts = self._session.scalars(
            select(Artifact)
            .where(
                Artifact.workspace_id == workspace_id,
                Artifact.task_id == task_id,
                Artifact.task_step_id.is_not(None),
            )
            .order_by(Artifact.created_at.asc())
        ).all()
        by_step: dict[UUID, list[Artifact]] = defaultdict(list)
        for artifact in artifacts:
            if artifact.task_step_id is not None:
                by_step[artifact.task_step_id].append(artifact)
        return by_step


def _summary(corrections: list[dict[str, object]]) -> dict[str, object]:
    status_counts = Counter(str(correction["status"]) for correction in corrections)
    mode_counts = Counter(str(correction["mode"]) for correction in corrections)
    blocked = [correction for correction in corrections if correction["blocked_reasons"]]
    return {
        "total_corrections": len(corrections),
        "status_counts": dict(sorted(status_counts.items())),
        "mode_counts": dict(sorted(mode_counts.items())),
        "blocked_corrections": len(blocked),
        "blocked_message_ids": [correction["message_id"] for correction in blocked],
    }


def _correction_status(
    task: Task,
    mode: str,
    step: TaskStep | None,
    runs: list[AgentRun],
) -> str:
    if mode == "stop_work":
        return "cancelled" if task.status == "cancelled" else "recorded"
    if step is None:
        return "follow_up_missing"
    if step.status == "completed":
        return "completed"
    if step.status in {"failed", "cancelled"}:
        return step.status
    if any(run.status in ACTIVE_RUN_STATUSES for run in runs):
        return "in_progress"
    if step.status == "queued":
        return "pending"
    return step.status


def _blocked_reasons(
    *,
    task: Task,
    mode: str,
    target: dict[str, object],
    created_step_id: UUID | None,
    step: TaskStep | None,
    runs: list[AgentRun],
    artifacts: list[Artifact],
) -> list[str]:
    reasons: list[str] = []
    if mode != "stop_work" and created_step_id is None:
        reasons.append("follow_up_step_not_created")
    if created_step_id is not None and step is None:
        reasons.append("follow_up_step_missing")
    if step is not None and step.status == "failed":
        reasons.append("follow_up_failed")
    if step is not None and step.status == "queued" and not runs:
        reasons.append("follow_up_waiting_to_start")
    if any(run.status == RunStatus.FAILED.value for run in runs):
        reasons.append("follow_up_run_failed")
    if (
        mode == "replace_artifact"
        and step is not None
        and step.status == "completed"
        and not artifacts
    ):
        reasons.append("replacement_artifact_missing")
    if mode == "stop_work" and task.status != "cancelled":
        reasons.append("task_not_cancelled")
    if not target:
        reasons.append("target_metadata_missing")
    return reasons


def _step_payload(step: TaskStep | None) -> dict[str, object] | None:
    if step is None:
        return None
    return {
        "id": step.id,
        "work_package_id": step.work_package_id,
        "title": step.title,
        "status": step.status,
        "order_index": step.order_index,
        "expected_artifacts": step.expected_artifacts,
        "result_summary": step.result_summary,
    }


def _run_payload(run: AgentRun) -> dict[str, object]:
    return {
        "id": run.id,
        "status": run.status,
        "started_at": run.started_at,
        "completed_at": run.completed_at,
        "error": redact_sensitive_payload(run.error) if isinstance(run.error, dict) else None,
    }


def _artifact_payload(artifact: Artifact) -> dict[str, object]:
    return {
        "id": artifact.id,
        "filename": artifact.filename,
        "artifact_type": artifact.artifact_type,
        "content_type": artifact.content_type,
        "version": artifact.version,
        "review_status": artifact.review_status,
        "work_package_id": artifact.work_package_id,
    }
