from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.dialects.sqlite import JSON as SqliteJSON
from sqlalchemy.orm import Session, sessionmaker

from backend.app.agent_runtime.contracts import AgentRuntimeExecutionBinding
from backend.app.agents.models import AgentProfile
from backend.app.capabilities.effective_catalog import (
    EffectiveCapabilityCatalogService,
    effective_catalog_fingerprint,
)
from backend.app.capabilities.models import (
    CapabilityResource,
    McpServer,
    McpToolAllowlist,
)
from backend.app.db import models as registered_models  # noqa: F401
from backend.app.db.base import Base
from backend.app.files.models import WorkspaceFile
from backend.app.identity.models import User
from backend.app.orchestration.run_authorization_integrity import (
    authorization_snapshot_fingerprint,
)
from backend.app.orchestration.run_request_authorization import RunAuthorizationService
from backend.app.orchestration.run_runtime_authorization import (
    RunRuntimeAuthorizationError,
    RunRuntimeAuthorizationService,
)
from backend.app.runs.models import AgentRun, RunEvent
from backend.app.runtime_spaces.models import RuntimeSpace
from backend.app.runtimes.models import WorkspaceRuntime
from backend.app.security.models import SecurityEvent
from backend.app.tasks.models import Task, TaskStep
from backend.app.teams.models import AgentTeam
from backend.app.workspaces.models import Workspace, WorkspaceMember


def test_effective_runtime_and_file_resources_freeze_execution_binding() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    runtime_space = RuntimeSpace(workspace_id=workspace.id, name="Isolated", status="active")
    session.add(runtime_space)
    session.flush()
    runtime = WorkspaceRuntime(
        workspace_id=workspace.id,
        runtime_space_id=runtime_space.id,
        name="Runtime",
        status="running",
        connection_status="online",
        network_policy={"disabled": True},
    )
    workspace_file = WorkspaceFile(
        workspace_id=workspace.id,
        uploaded_by_user_id=user.id,
        filename="brief.txt",
        content_type="text/plain",
        size_bytes=5,
        checksum_sha256="a" * 64,
        storage_key="workspaces/test/brief.txt",
    )
    task = Task(workspace_id=workspace.id, created_by_user_id=user.id, title="Task")
    session.add_all([runtime, workspace_file, task])
    session.flush()
    step = TaskStep(
        workspace_id=workspace.id,
        task_id=task.id,
        title="Step",
        dependencies={"allowed_file_ids": [str(workspace_file.id)]},
    )
    session.add(step)
    session.flush()
    runtime_resource_id = uuid4()
    file_resource_id = uuid4()
    catalog = _catalog(
        workspace.id,
        resources=[
            _resource_entry(
                runtime_resource_id,
                "runtime",
                "execute",
                {"workspace_runtime_id": str(runtime.id)},
            ),
            _resource_entry(
                file_resource_id,
                "file_collection",
                "read",
                {"file_ids": [str(workspace_file.id)]},
            ),
        ],
    )

    binding = RunRuntimeAuthorizationService(session).resolve_for_snapshot(
        task=task,
        step=step,
        capability_catalog=catalog,
        runtime_policy={"mcp": {"network_mode": "none"}},
    )

    assert binding.mode == "capability_runtime"
    assert binding.workspace_runtime_id == runtime.id
    assert binding.runtime_space_id == runtime_space.id
    assert binding.capability_resource_ids == (runtime_resource_id,)
    assert binding.allowed_file_ids == (workspace_file.id,)
    assert binding.network_disabled is True
    assert binding.as_snapshot()["file_access_scope"] == {
        "mode": "gateway_only",
        "allowed_file_ids": [str(workspace_file.id)],
    }
    assert binding.as_runtime_context() == AgentRuntimeExecutionBinding(
        mode="capability_runtime",
        workspace_runtime_id=runtime.id,
        runtime_space_id=runtime_space.id,
        capability_resource_ids=(runtime_resource_id,),
        network_disabled=True,
        allowed_file_ids=(workspace_file.id,),
    )


def test_ambiguous_runtime_resource_placement_is_rejected() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    task = Task(workspace_id=workspace.id, created_by_user_id=user.id, title="Task")
    session.add(task)
    session.flush()
    step = TaskStep(workspace_id=workspace.id, task_id=task.id, title="Step")
    session.add(step)
    session.flush()
    catalog = _catalog(
        workspace.id,
        resources=[
            _resource_entry(
                uuid4(),
                "runtime",
                "execute",
                {"workspace_runtime_id": str(uuid4())},
            ),
            _resource_entry(
                uuid4(),
                "runtime",
                "execute",
                {"workspace_runtime_id": str(uuid4())},
            ),
        ],
    )

    with pytest.raises(RunRuntimeAuthorizationError) as exc_info:
        RunRuntimeAuthorizationService(session).resolve_for_snapshot(
            task=task,
            step=step,
            capability_catalog=catalog,
            runtime_policy={},
        )

    assert exc_info.value.code == "runtime_grant_ambiguous"


def test_stdio_tool_requires_concrete_authorized_runtime() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    profile = AgentProfile(
        workspace_id=workspace.id,
        name="Agent",
        role="worker",
        tool_policy={"allowed_tools": ["local_tool"]},
    )
    server = McpServer(
        workspace_id=workspace.id,
        name="Local MCP",
        server_type="stdio",
        connection={"command": ["local-mcp"]},
    )
    session.add_all([profile, server])
    session.flush()
    session.add(
        McpToolAllowlist(
            workspace_id=workspace.id,
            mcp_server_id=server.id,
            tool_name="local_tool",
            description="Run the local tool",
            input_schema={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        )
    )
    task = Task(workspace_id=workspace.id, created_by_user_id=user.id, title="Task")
    session.add(task)
    session.flush()
    step = TaskStep(workspace_id=workspace.id, task_id=task.id, title="Step")
    session.add(step)
    session.flush()
    catalog = (
        EffectiveCapabilityCatalogService(session)
        .build(workspace_id=workspace.id, agent_profile_id=profile.id)
        .model_dump(mode="json")
    )

    assert catalog["tools"][0]["descriptor"]["mcp_server_type"] == "stdio"

    with pytest.raises(RunRuntimeAuthorizationError) as exc_info:
        RunRuntimeAuthorizationService(session).resolve_for_snapshot(
            task=task,
            step=step,
            capability_catalog=catalog,
            runtime_policy={},
        )

    assert exc_info.value.code == "stdio_runtime_required"


def test_runtime_revocation_blocks_worker_request_and_records_audit() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    profile = AgentProfile(workspace_id=workspace.id, name="Agent", role="worker")
    runtime_space = RuntimeSpace(workspace_id=workspace.id, name="Isolated", status="active")
    session.add(runtime_space)
    session.flush()
    runtime = WorkspaceRuntime(
        workspace_id=workspace.id,
        runtime_space_id=runtime_space.id,
        name="Runtime",
        status="running",
        connection_status="online",
        network_policy={"disabled": True},
    )
    task = Task(workspace_id=workspace.id, created_by_user_id=user.id, title="Task")
    session.add_all([profile, runtime, task])
    session.flush()
    step = TaskStep(
        workspace_id=workspace.id,
        task_id=task.id,
        assigned_agent_profile_id=profile.id,
        title="Step",
    )
    runtime_resource = CapabilityResource(
        workspace_id=workspace.id,
        key="isolated-runtime",
        name="Isolated runtime",
        resource_type="runtime",
        access_mode="execute",
        locator={"workspace_runtime_id": str(runtime.id)},
    )
    session.add_all([step, runtime_resource])
    session.flush()
    catalog = _catalog(
        workspace.id,
        agent_profile_id=profile.id,
        resources=[
            _resource_entry(
                runtime_resource.id,
                "runtime",
                "execute",
                {"workspace_runtime_id": str(runtime.id)},
                version=runtime_resource.version,
            )
        ],
    )
    catalog["fingerprint"] = effective_catalog_fingerprint(catalog)
    binding = RunRuntimeAuthorizationService(session).resolve_for_snapshot(
        task=task,
        step=step,
        capability_catalog=catalog,
        runtime_policy={"network": "disabled"},
    )
    snapshot = _snapshot(
        workspace_id=workspace.id,
        task=task,
        step=step,
        profile=profile,
        catalog=catalog,
        binding=binding.as_snapshot(),
    )
    run = AgentRun(
        workspace_id=workspace.id,
        task_id=task.id,
        task_step_id=step.id,
        agent_profile_id=profile.id,
        runtime_id=runtime.id,
        runtime_space_id=runtime_space.id,
        input={"authorization_snapshot": snapshot},
    )
    session.add(run)
    session.commit()
    runtime.status = "stopped"
    runtime.connection_status = "offline"
    session.flush()

    with pytest.raises(RunRuntimeAuthorizationError) as exc_info:
        RunAuthorizationService(session).validate_authorization_snapshot(
            run,
            task,
            profile,
            snapshot,
        )

    assert exc_info.value.code == "runtime_unavailable"
    event = session.scalar(
        select(RunEvent).where(
            RunEvent.agent_run_id == run.id,
            RunEvent.event_type == "runtime.authorization_blocked",
        )
    )
    security_event = session.scalar(
        select(SecurityEvent).where(
            SecurityEvent.workspace_id == workspace.id,
            SecurityEvent.action == "agent_runtime.authorization_blocked",
        )
    )
    assert event is not None
    assert event.event_metadata["reason"] == "runtime_unavailable"
    assert security_event is not None
    assert security_event.reason == "runtime_unavailable"


def test_file_grant_without_frozen_runtime_binding_is_rejected() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    profile = AgentProfile(workspace_id=workspace.id, name="Agent", role="worker")
    workspace_file = WorkspaceFile(
        workspace_id=workspace.id,
        uploaded_by_user_id=user.id,
        filename="brief.txt",
        content_type="text/plain",
        size_bytes=5,
        checksum_sha256="b" * 64,
        storage_key="workspaces/test/brief.txt",
    )
    task = Task(workspace_id=workspace.id, created_by_user_id=user.id, title="Task")
    session.add_all([profile, workspace_file, task])
    session.flush()
    step = TaskStep(
        workspace_id=workspace.id,
        task_id=task.id,
        assigned_agent_profile_id=profile.id,
        title="Step",
    )
    session.add(step)
    session.flush()
    catalog = _catalog(
        workspace.id,
        agent_profile_id=profile.id,
        resources=[
            _resource_entry(
                uuid4(),
                "file_collection",
                "read",
                {"file_ids": [str(workspace_file.id)]},
            )
        ],
    )
    snapshot: dict[str, object] = {
        "version": 2,
        "workspace_id": str(workspace.id),
        "task_id": str(task.id),
        "task_step_id": str(step.id),
        "agent_profile_id": str(profile.id),
        "runtime_space_id": None,
        "allowed_tools": [],
        "capability_catalog": catalog,
        "file_scope": {
            "mode": "authorized_file_resources",
            "workspace_id": str(workspace.id),
            "task_id": str(task.id),
            "allowed_file_ids": [str(workspace_file.id)],
        },
    }
    snapshot["fingerprint"] = authorization_snapshot_fingerprint(snapshot)
    run = AgentRun(
        workspace_id=workspace.id,
        task_id=task.id,
        task_step_id=step.id,
        agent_profile_id=profile.id,
        input={"authorization_snapshot": snapshot},
    )
    session.add(run)
    session.flush()

    with pytest.raises(RunRuntimeAuthorizationError) as exc_info:
        RunAuthorizationService(session).validate_authorization_snapshot(
            run,
            task,
            profile,
            snapshot,
        )

    assert exc_info.value.code == "runtime_binding_missing"
    assert (
        session.scalar(
            select(RunEvent).where(
                RunEvent.agent_run_id == run.id,
                RunEvent.event_type == "runtime.authorization_blocked",
            )
        )
        is not None
    )
    assert (
        session.scalar(
            select(SecurityEvent).where(
                SecurityEvent.workspace_id == workspace.id,
                SecurityEvent.action == "agent_runtime.authorization_blocked",
                SecurityEvent.reason == "runtime_binding_missing",
            )
        )
        is not None
    )


def test_team_runtime_unbinding_revokes_frozen_run() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    runtime = WorkspaceRuntime(
        workspace_id=workspace.id,
        name="Team runtime",
        status="running",
        connection_status="online",
        network_policy={"disabled": True},
    )
    session.add(runtime)
    session.flush()
    team = AgentTeam(
        workspace_id=workspace.id,
        name="Team",
        team_type="delivery",
        default_task_policy={
            "team_runtime": {"workspace_runtime_id": str(runtime.id)},
        },
    )
    session.add(team)
    session.flush()
    task = Task(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        agent_team_id=team.id,
        title="Task",
    )
    session.add(task)
    session.flush()
    step = TaskStep(workspace_id=workspace.id, task_id=task.id, title="Step")
    session.add(step)
    session.flush()
    binding = RunRuntimeAuthorizationService(session).resolve_for_snapshot(
        task=task,
        step=step,
        capability_catalog=None,
        runtime_policy={},
    )
    snapshot = {
        "capability_catalog": None,
        "file_scope": {"allowed_file_ids": []},
        "runtime_binding": binding.as_snapshot(),
    }
    run = AgentRun(
        workspace_id=workspace.id,
        task_id=task.id,
        task_step_id=step.id,
        runtime_id=runtime.id,
        input={"authorization_snapshot": snapshot},
    )
    session.add(run)
    session.flush()
    team.default_task_policy = {}
    session.flush()

    with pytest.raises(RunRuntimeAuthorizationError) as exc_info:
        RunRuntimeAuthorizationService(session).validate_for_run(
            run=run,
            task=task,
            snapshot=snapshot,
        )

    assert exc_info.value.code == "team_runtime_binding_revoked"


def _snapshot(
    *,
    workspace_id,
    task: Task,
    step: TaskStep,
    profile: AgentProfile,
    catalog: dict[str, object],
    binding: dict[str, object],
) -> dict[str, object]:
    snapshot: dict[str, object] = {
        "version": 2,
        "workspace_id": str(workspace_id),
        "task_id": str(task.id),
        "task_step_id": str(step.id),
        "agent_profile_id": str(profile.id),
        "runtime_space_id": binding["runtime_space_id"],
        "allowed_tools": [],
        "capability_catalog": catalog,
        "file_scope": {
            "mode": "authorized_file_resources",
            "workspace_id": str(workspace_id),
            "task_id": str(task.id),
            "allowed_file_ids": [],
        },
        "runtime_binding": binding,
    }
    snapshot["fingerprint"] = authorization_snapshot_fingerprint(snapshot)
    return snapshot


def _catalog(
    workspace_id,
    *,
    agent_profile_id=None,
    resources: list[dict[str, object]],
) -> dict[str, object]:
    catalog: dict[str, object] = {
        "catalog_version": 1,
        "workspace_id": str(workspace_id),
        "agent_profile_id": str(agent_profile_id or uuid4()),
        "agent_profile_version": 1,
        "team_id": None,
        "team_policy_version": None,
        "team_member_id": None,
        "department": None,
        "tools": [],
        "resources": resources,
        "denied": [],
    }
    catalog["fingerprint"] = effective_catalog_fingerprint(catalog)
    return catalog


def _resource_entry(
    resource_id,
    resource_type: str,
    access_mode: str,
    locator: dict[str, object],
    *,
    version: int = 1,
) -> dict[str, object]:
    return {
        "resource": {
            "id": str(resource_id),
            "key": str(resource_id),
            "name": resource_type,
            "resource_type": resource_type,
            "access_mode": access_mode,
            "locator": locator,
            "parameter_schema": {},
            "default_parameters": {},
            "status": "active",
            "version": version,
        },
        "parameters": {},
        "locked_parameters": [],
        "provenance": [],
    }


def _session() -> Session:
    _patch_portable_types_for_sqlite()
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


def _seed_workspace(session: Session) -> tuple[User, Workspace]:
    user = User(email=f"{uuid4()}@example.com", display_name="Owner")
    workspace = Workspace(owner=user, name="Acme", slug=str(uuid4()), settings={})
    session.add_all(
        [user, workspace, WorkspaceMember(workspace=workspace, user=user, role="owner")]
    )
    session.commit()
    return user, workspace


def _patch_portable_types_for_sqlite() -> None:
    for table in Base.metadata.tables.values():
        for column in table.columns:
            if isinstance(column.type, PostgresUUID):
                column.type = column.type.as_generic()
            if isinstance(column.type, JSONB):
                column.type = SqliteJSON()
