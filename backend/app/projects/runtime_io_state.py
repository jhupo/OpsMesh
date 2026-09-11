from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.artifacts.models import Artifact
from backend.app.audit.service import AuditService
from backend.app.files.models import FileAccessEvent
from backend.app.orchestration.run_events import RunEventRecorder
from backend.app.projects.models import AgentRunProjectIOState, AgentRunProjectSnapshot
from backend.app.projects.run_manifest import RunProjectManifest, parse_run_project_manifest
from backend.app.projects.runtime_io_errors import ProjectRunIOError
from backend.app.runs.models import AgentRun
from backend.app.runtime_manager.models import WorkspaceRuntime
from backend.app.security.models import SecurityEvent


class ProjectIOStateService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def lock_or_create(
        self,
        run: AgentRun,
        snapshot: AgentRunProjectSnapshot,
        runtime: WorkspaceRuntime,
        root_path: str,
        *,
        stage: str,
    ) -> AgentRunProjectIOState:
        state = self.locked(run)
        if state is None:
            state = AgentRunProjectIOState(
                workspace_id=run.workspace_id,
                agent_run_id=run.id,
                project_snapshot_id=snapshot.id,
                workspace_runtime_id=runtime.id,
                root_path=root_path,
                status="pending",
            )
            self._session.add(state)
            self._session.flush([state])
            return state
        if (
            state.project_snapshot_id != snapshot.id
            or state.workspace_runtime_id != runtime.id
            or state.root_path != root_path
        ):
            raise ProjectRunIOError(
                code="project_io_state_conflict",
                message="Run project I/O state does not match its immutable bindings",
                stage=stage,
                retryable=False,
            )
        return state

    def locked(self, run: AgentRun) -> AgentRunProjectIOState | None:
        return self._session.scalar(
            select(AgentRunProjectIOState)
            .where(
                AgentRunProjectIOState.workspace_id == run.workspace_id,
                AgentRunProjectIOState.agent_run_id == run.id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    def get(self, run: AgentRun) -> AgentRunProjectIOState | None:
        return self._session.scalar(
            select(AgentRunProjectIOState).where(
                AgentRunProjectIOState.workspace_id == run.workspace_id,
                AgentRunProjectIOState.agent_run_id == run.id,
            )
        )

    def snapshot_for_state(
        self,
        run: AgentRun,
        state: AgentRunProjectIOState,
        *,
        stage: str,
    ) -> AgentRunProjectSnapshot:
        snapshot = self._session.scalar(
            select(AgentRunProjectSnapshot).where(
                AgentRunProjectSnapshot.workspace_id == run.workspace_id,
                AgentRunProjectSnapshot.agent_run_id == run.id,
                AgentRunProjectSnapshot.id == state.project_snapshot_id,
            )
        )
        if snapshot is None:
            raise ProjectRunIOError(
                code="project_snapshot_missing",
                message="Run project snapshot is unavailable",
                stage=stage,
                retryable=False,
            )
        return snapshot

    @staticmethod
    def manifest(
        snapshot: AgentRunProjectSnapshot,
        run: AgentRun,
        *,
        stage: str,
    ) -> RunProjectManifest:
        try:
            return parse_run_project_manifest(snapshot, workspace_id=run.workspace_id)
        except ValueError as exc:
            raise ProjectRunIOError(
                code="project_snapshot_invalid",
                message="Run project snapshot failed integrity validation",
                stage=stage,
                retryable=False,
                metadata={"boundary_denial": True},
            ) from exc

    def mark_inputs_staged(
        self,
        *,
        run: AgentRun,
        state: AgentRunProjectIOState,
        snapshot: AgentRunProjectSnapshot,
        runtime: WorkspaceRuntime,
        manifest: RunProjectManifest,
        file_count: int,
        total_bytes: int,
        actor_user_id: UUID | None,
    ) -> None:
        now = datetime.now(UTC)
        state.status = "staged"
        state.staged_file_count = file_count
        state.staged_bytes = total_bytes
        state.error = None
        state.staged_at = now
        self._bind_state_to_run(run, state, snapshot)
        for item in manifest.files:
            self._session.add(
                FileAccessEvent(
                    workspace_id=run.workspace_id,
                    workspace_file_id=item.workspace_file_id,
                    artifact_id=None,
                    user_id=actor_user_id,
                    action="stage_to_runtime",
                    created_at=now,
                )
            )
        metadata: dict[str, object] = {
            "project_snapshot_id": str(snapshot.id),
            "workspace_runtime_id": str(runtime.id),
            "root_path": state.root_path,
            "file_count": file_count,
            "total_bytes": total_bytes,
            "fingerprint_sha256": snapshot.fingerprint_sha256,
        }
        RunEventRecorder(self._session).append_event(
            run,
            "project.inputs_staged",
            "Project inputs staged into the authorized runtime",
            metadata,
        )
        AuditService(self._session).record_system_action(
            workspace_id=run.workspace_id,
            action="project.inputs_staged",
            target_type="agent_run",
            target_id=run.id,
            metadata=metadata,
        )
        self._session.commit()
        self._session.refresh(state)

    def mark_outputs_harvested(
        self,
        run: AgentRun,
        snapshot: AgentRunProjectSnapshot,
        state: AgentRunProjectIOState,
        artifacts: list[Artifact],
    ) -> None:
        state.status = "harvested"
        state.harvested_output_count = len(artifacts)
        state.harvested_bytes = sum(artifact.size_bytes for artifact in artifacts)
        state.error = None
        state.harvested_at = datetime.now(UTC)
        metadata: dict[str, object] = {
            "project_snapshot_id": str(snapshot.id),
            "artifact_ids": [str(artifact.id) for artifact in artifacts],
            "output_count": len(artifacts),
            "total_bytes": state.harvested_bytes,
        }
        RunEventRecorder(self._session).append_event(
            run,
            "project.outputs_harvested",
            "Declared project outputs were persisted as artifacts",
            metadata,
        )
        AuditService(self._session).record_system_action(
            workspace_id=run.workspace_id,
            action="project.outputs_harvested",
            target_type="agent_run",
            target_id=run.id,
            metadata=metadata,
        )

    def record_failure(
        self,
        run: AgentRun,
        state: AgentRunProjectIOState,
        error: ProjectRunIOError,
    ) -> None:
        state.status = "failed"
        state.error = {
            "code": error.code,
            "message": error.message,
            "retryable": error.retryable,
        }
        RunEventRecorder(self._session).append_event(
            run,
            error.event_type,
            error.message,
            error.metadata,
        )
        AuditService(self._session).record_system_action(
            workspace_id=run.workspace_id,
            action=error.event_type,
            target_type="agent_run",
            target_id=run.id,
            metadata={"code": error.code, **error.metadata},
        )
        if error.metadata.get("boundary_denial") is True:
            self._session.add(
                SecurityEvent(
                    workspace_id=run.workspace_id,
                    user_id=None,
                    action="project.file_boundary_blocked",
                    outcome="blocked",
                    severity="high",
                    source_ip=None,
                    user_agent=None,
                    request_id=None,
                    path="internal:project_file_gateway",
                    method="WORKER",
                    reason=error.code,
                    event_metadata={
                        "agent_run_id": str(run.id),
                        "stage": error.metadata.get("stage"),
                    },
                    created_at=datetime.now(UTC),
                )
            )
        self._session.commit()

    def harvested_artifacts(self, run: AgentRun) -> list[Artifact]:
        return list(
            self._session.scalars(
                select(Artifact)
                .where(
                    Artifact.workspace_id == run.workspace_id,
                    Artifact.agent_run_id == run.id,
                    Artifact.workspace_project_output_id.is_not(None),
                )
                .order_by(Artifact.project_path.asc())
            )
        )

    def begin_cleanup(self, run: AgentRun) -> AgentRunProjectIOState | None:
        state = self.locked(run)
        if state is None or state.cleanup_status in {"completed", "not_required"}:
            return state
        state.cleanup_status = "running"
        state.cleanup_attempts += 1
        state.cleanup_error = None
        self._session.flush([state])
        return state

    def mark_cleanup_completed(
        self,
        run: AgentRun,
        state: AgentRunProjectIOState,
        *,
        status: str = "completed",
        error: dict[str, object] | None = None,
    ) -> None:
        state.cleanup_status = status
        state.cleanup_error = error
        state.cleaned_at = datetime.now(UTC) if status in {"completed", "not_required"} else None
        RunEventRecorder(self._session).append_event(
            run,
            "project.runtime_cleanup.completed"
            if status in {"completed", "not_required"}
            else "project.runtime_cleanup.failed",
            "Per-run runtime workspace cleanup completed"
            if status in {"completed", "not_required"}
            else "Per-run runtime workspace cleanup failed",
            {
                "cleanup_status": status,
                "cleanup_attempts": state.cleanup_attempts,
                "root_path": state.root_path,
                **({"error_code": error.get("code")} if error else {}),
            },
        )
        AuditService(self._session).record_system_action(
            workspace_id=run.workspace_id,
            action=(
                "project.runtime_cleanup.completed"
                if status in {"completed", "not_required"}
                else "project.runtime_cleanup.failed"
            ),
            target_type="agent_run",
            target_id=run.id,
            metadata={
                "cleanup_status": status,
                "cleanup_attempts": state.cleanup_attempts,
                "root_path": state.root_path,
                **({"error_code": error.get("code")} if error else {}),
            },
        )

    @staticmethod
    def _bind_state_to_run(
        run: AgentRun,
        state: AgentRunProjectIOState,
        snapshot: AgentRunProjectSnapshot,
    ) -> None:
        run.input = {
            **(run.input or {}),
            "project_io": {
                "state_id": str(state.id),
                "root_path": state.root_path,
                "project_snapshot_id": str(snapshot.id),
                "fingerprint_sha256": snapshot.fingerprint_sha256,
            },
        }
