from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.orm import Session

from backend.app.security.redaction import redact_sensitive_payload, redact_sensitive_text
from backend.app.teams.runtime_constants import (
    TEAM_RUNTIME_STALL_STATUSES,
    TEAM_RUNTIME_STATUS_KEY,
)
from backend.app.teams.runtime_mailbox import TeamRuntimeMailboxStore
from backend.app.teams.runtime_refs import team_runtime_metadata
from backend.app.teams.runtime_repository import TeamRuntimeRepository
from backend.app.teams.runtime_state_utils import _int, _stall_metadata_update


class TeamRuntimeHeartbeatRecorder:
    """Record runtime loop heartbeats and worker failure diagnostics."""

    def __init__(
        self,
        *,
        session: Session,
        repo: TeamRuntimeRepository,
        mailbox: TeamRuntimeMailboxStore,
    ) -> None:
        self._session = session
        self._repo = repo
        self._mailbox = mailbox

    def record_iteration(
        self,
        *,
        workspace_id: UUID,
        team_id: UUID,
        actor_user_id: UUID,
        status: str,
        summary: dict[str, object],
    ) -> None:
        team = self._repo.team(workspace_id, team_id)
        if team is None:
            return
        policy = dict(team.default_task_policy or {})
        runtime_metadata = team_runtime_metadata(team)
        recorded_at = datetime.now(UTC).isoformat()
        iteration_count = _int(runtime_metadata.get("iteration_count")) + 1
        last_iteration: dict[str, object] = {
            "iteration": iteration_count,
            "status": status,
            "summary": dict(summary),
            "recorded_at": recorded_at,
            "actor_user_id": str(actor_user_id),
        }
        runtime_metadata.update(
            {
                "last_iteration": last_iteration,
                "iteration_count": iteration_count,
                "last_heartbeat_at": recorded_at,
                "heartbeat_status": status,
                **_stall_metadata_update(
                    runtime_metadata=runtime_metadata,
                    status=status,
                    summary=summary,
                    recorded_at=recorded_at,
                ),
            }
        )
        if status not in TEAM_RUNTIME_STALL_STATUSES:
            for key in ("stall_count", "stall_reason", "stalled_at", "stall_threshold"):
                runtime_metadata.pop(key, None)
        runtime_metadata.pop("last_worker_failure", None)
        policy[TEAM_RUNTIME_STATUS_KEY] = runtime_metadata
        team.default_task_policy = policy

        thread = self._mailbox._get_or_create_thread(team)
        self._mailbox._append_runtime_message(
            team=team,
            thread=thread,
            actor_user_id=actor_user_id,
            message_type="runtime_iteration",
            body=f"Team execution loop iteration: {status}",
            payload={"status": status, "summary": summary, "iteration": last_iteration},
        )
        self._session.flush()

    def record_worker_failure(
        self,
        *,
        workspace_id: UUID,
        team_id: UUID,
        worker_id: str,
        queue_name: str,
        job_id: UUID,
        status: str,
        attempt: int,
        max_attempts: int,
        will_retry: bool,
        error: BaseException,
        actor_user_id: UUID | None = None,
        routing: dict[str, object] | None = None,
        trace_metadata: dict[str, object] | None = None,
    ) -> None:
        team = self._repo.team(workspace_id, team_id)
        if team is None:
            return
        policy = dict(team.default_task_policy or {})
        runtime_metadata = team_runtime_metadata(team)
        recorded_at = datetime.now(UTC).isoformat()
        failure = {
            "status": status,
            "recorded_at": recorded_at,
            "worker_id": worker_id,
            "queue_name": queue_name,
            "job_id": str(job_id),
            "attempt": attempt,
            "max_attempts": max_attempts,
            "will_retry": will_retry,
            "error_type": type(error).__name__,
            "error": redact_sensitive_text(str(error)),
            "routing": redact_sensitive_payload(dict(routing or {})),
            "trace": redact_sensitive_payload(dict(trace_metadata or {})),
        }
        runtime_metadata.update(
            {
                "last_worker_failure": failure,
                "heartbeat_status": status,
                "last_heartbeat_at": recorded_at,
            }
        )
        policy[TEAM_RUNTIME_STATUS_KEY] = runtime_metadata
        team.default_task_policy = policy

        thread = self._mailbox._get_or_create_thread(team)
        self._mailbox._append_runtime_message(
            team=team,
            thread=thread,
            actor_user_id=actor_user_id,
            message_type=f"team.runtime.worker_{status}",
            body=f"Team execution loop worker {status}",
            payload={"status": status, "worker_failure": failure},
        )
        self._session.flush()
