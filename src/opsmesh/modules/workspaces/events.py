"""Workspace persistence facts."""

from typing import Any, Literal

from opsmesh.framework.typing import OpsMeshContract


class WorkspaceSnapshotCreated(OpsMeshContract):
    type: Literal["workspace.snapshot.created"] = "workspace.snapshot.created"
    source: Literal["workspaces"] = "workspaces"
    payload: dict[str, Any]
    schema_version: Literal[1] = 1
