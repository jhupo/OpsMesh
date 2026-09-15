from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.domains.agents.runtime.errors import AgentRuntimePolicyError
from backend.app.domains.orchestration.runs.models import AgentRun
from backend.app.domains.workspace.projects.models import AgentRunProjectIOState
from backend.app.domains.workspace.projects.snapshots.manifest import (
    parse_run_project_manifest,
    public_run_project_manifest,
)
from backend.app.domains.workspace.projects.snapshots.service import RunProjectSnapshotService


class ProjectRunIOError(AgentRuntimePolicyError):
    def __init__(
        self,
        *,
        code: str,
        message: str,
        stage: str,
        retryable: bool,
        metadata: dict[str, object] | None = None,
    ) -> None:
        super().__init__(
            code=code,
            message=message,
            event_type=f"project.{stage}.failed",
            metadata={"stage": stage, **(metadata or {})},
            retryable=retryable,
        )
class RunProjectIOQueryService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_state(
        self,
        workspace_id: UUID,
        run_id: UUID,
    ) -> AgentRunProjectIOState | None:
        return self._session.scalar(
            select(AgentRunProjectIOState).where(
                AgentRunProjectIOState.workspace_id == workspace_id,
                AgentRunProjectIOState.agent_run_id == run_id,
            )
        )
def project_runtime_context(
    session: Session,
    run: AgentRun,
) -> dict[str, object] | None:
    state = session.scalar(
        select(AgentRunProjectIOState).where(
            AgentRunProjectIOState.workspace_id == run.workspace_id,
            AgentRunProjectIOState.agent_run_id == run.id,
            AgentRunProjectIOState.status.in_(("staged", "harvesting", "harvested")),
        )
    )
    if state is None:
        return None
    snapshot = RunProjectSnapshotService(session).get_for_run(run.workspace_id, run.id)
    if snapshot is None or snapshot.id != state.project_snapshot_id:
        raise ValueError("Run project I/O state has no matching snapshot")
    manifest = parse_run_project_manifest(snapshot, workspace_id=run.workspace_id)
    return {
        "root_path": state.root_path,
        "state_id": str(state.id),
        "snapshot_id": str(snapshot.id),
        "fingerprint_sha256": snapshot.fingerprint_sha256,
        **public_run_project_manifest(manifest),
    }

