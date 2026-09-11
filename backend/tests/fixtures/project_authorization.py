from __future__ import annotations

from uuid import uuid4

from sqlalchemy.orm import Session

from backend.app.capabilities.effective_catalog import effective_catalog_fingerprint
from backend.app.capabilities.models import CapabilityResource
from backend.app.files.models import WorkspaceFile
from backend.app.orchestration.run_authorization_integrity import (
    authorization_snapshot_fingerprint,
)
from backend.app.runs.models import AgentRun
from backend.app.runtime_manager.models import WorkspaceRuntime
from backend.app.tasks.models import Task


def authorize_project_run(
    session: Session,
    *,
    run: AgentRun,
    task: Task,
    runtime: WorkspaceRuntime,
    files: list[WorkspaceFile],
) -> None:
    runtime_resource = CapabilityResource(
        workspace_id=run.workspace_id,
        key=f"runtime-{uuid4()}",
        name="Project runtime",
        resource_type="runtime",
        access_mode="execute",
        locator={"workspace_runtime_id": str(runtime.id)},
    )
    file_resource = CapabilityResource(
        workspace_id=run.workspace_id,
        key=f"files-{uuid4()}",
        name="Project inputs",
        resource_type="file_collection",
        access_mode="read",
        locator={"file_ids": [str(file.id) for file in files]},
    )
    session.add_all([runtime_resource, file_resource])
    session.flush()
    resources = [
        _resource_entry(runtime_resource),
        _resource_entry(file_resource),
    ]
    catalog: dict[str, object] = {
        "catalog_version": 1,
        "workspace_id": str(run.workspace_id),
        "agent_profile_id": None,
        "agent_profile_version": 0,
        "team_id": None,
        "team_policy_version": None,
        "team_member_id": None,
        "department": None,
        "tools": [],
        "resources": resources,
        "denied": [],
    }
    catalog["fingerprint"] = effective_catalog_fingerprint(catalog)
    file_ids = [str(file.id) for file in files]
    binding: dict[str, object] = {
        "mode": "capability_runtime",
        "workspace_id": str(run.workspace_id),
        "workspace_runtime_id": str(runtime.id),
        "runtime_space_id": None,
        "capability_resource_ids": [str(runtime_resource.id)],
        "network_disabled": False,
        "file_access_scope": {
            "mode": "gateway_only",
            "allowed_file_ids": file_ids,
        },
    }
    snapshot: dict[str, object] = {
        "version": 2,
        "workspace_id": str(run.workspace_id),
        "task_id": str(task.id),
        "task_step_id": None,
        "agent_profile_id": None,
        "runtime_space_id": None,
        "allowed_tools": [],
        "capability_catalog": catalog,
        "file_scope": {
            "mode": "authorized_file_resources",
            "workspace_id": str(run.workspace_id),
            "task_id": str(task.id),
            "allowed_file_ids": file_ids,
        },
        "runtime_binding": binding,
    }
    snapshot["fingerprint"] = authorization_snapshot_fingerprint(snapshot)
    run.input = {"authorization_snapshot": snapshot}


def _resource_entry(resource: CapabilityResource) -> dict[str, object]:
    return {
        "resource": {
            "id": str(resource.id),
            "key": resource.key,
            "name": resource.name,
            "resource_type": resource.resource_type,
            "access_mode": resource.access_mode,
            "locator": resource.locator,
            "parameter_schema": {},
            "default_parameters": {},
            "status": resource.status,
            "version": resource.version,
        },
        "parameters": {},
        "locked_parameters": [],
        "provenance": [],
    }
