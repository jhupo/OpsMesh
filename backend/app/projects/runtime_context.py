from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.projects.models import AgentRunProjectIOState
from backend.app.projects.run_manifest import (
    parse_run_project_manifest,
    public_run_project_manifest,
)
from backend.app.projects.run_snapshots import RunProjectSnapshotService
from backend.app.runs.models import AgentRun


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
