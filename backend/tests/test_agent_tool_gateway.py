from hashlib import sha256
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.dialects.sqlite import JSON as SqliteJSON
from sqlalchemy.orm import Session, sessionmaker

from backend.app.agent_runtime.contracts import (
    AgentRuntimeContext,
    AgentRuntimeResourceGrant,
    AgentRuntimeToolDefinition,
)
from backend.app.agent_runtime.tool_gateway import AgentToolGateway, ToolGatewayDenied
from backend.app.agent_runtime.tools import BackendToolExecutor
from backend.app.agents.models import AgentProfile
from backend.app.capabilities.effective_catalog import EffectiveCapabilityCatalogService
from backend.app.capabilities.models import CapabilityResource, McpCredentialReference, McpServer
from backend.app.core.config import Settings
from backend.app.db import models as registered_models  # noqa: F401
from backend.app.db.base import Base
from backend.app.files.models import WorkspaceFile
from backend.app.files.storage import LocalStorage
from backend.app.identity.models import User
from backend.app.reviews.models import ResourceReview
from backend.app.reviews.service import ResourcePolicyReviewBuilder
from backend.app.runs.models import AgentRun, RunEvent
from backend.app.security.models import SecurityEvent
from backend.app.workspaces.models import Workspace, WorkspaceMember


@pytest.fixture(autouse=True)
def _approve_semantic_tool_execution_review(monkeypatch: pytest.MonkeyPatch) -> None:
    def approved_review(self: ResourcePolicyReviewBuilder, **_: object) -> ResourceReview:
        return ResourceReview(
            required=False,
            risk_level="low",
            reasons=["llm_review.approved"],
            signals={"reviewer": "test"},
        )

    monkeypatch.setattr(
        ResourcePolicyReviewBuilder,
        "review_tool_execution",
        approved_review,
    )


def test_effective_catalog_hides_product_tool_without_required_resource() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    profile = AgentProfile(
        workspace_id=workspace.id,
        name="Reader",
        role="researcher",
        tool_policy={"allowed_tools": ["read_workspace_file"]},
        capabilities={"resource_ids": []},
    )
    session.add(profile)
    session.commit()

    without_resource = EffectiveCapabilityCatalogService(session).build(
        workspace_id=workspace.id,
        agent_profile_id=profile.id,
    )

    assert without_resource.tools == []
    assert without_resource.denied[-1].key == "read_workspace_file"
    assert "file_collection" in without_resource.denied[-1].reason

    resource = CapabilityResource(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        key="project-files",
        name="Project files",
        resource_type="file_collection",
        access_mode="read",
        locator={"file_ids": []},
    )
    session.add(resource)
    session.flush()
    profile.capabilities = {"resource_ids": [str(resource.id)]}
    session.commit()

    with_resource = EffectiveCapabilityCatalogService(session).build(
        workspace_id=workspace.id,
        agent_profile_id=profile.id,
    )

    assert [item.descriptor.name for item in with_resource.tools] == [
        "read_workspace_file"
    ]
    assert with_resource.tools[0].descriptor.required_resource_type == "file_collection"
    assert with_resource.tools[0].descriptor.required_access_modes == ["read"]


def test_agent_tool_gateway_enforces_locked_parameters_and_live_resource_status() -> None:
    session = _session()
    _, workspace = _seed_workspace(session)
    run = AgentRun(workspace_id=workspace.id)
    resource = CapabilityResource(
        workspace_id=workspace.id,
        key="locked-files",
        name="Locked files",
        resource_type="file_collection",
        access_mode="read",
        locator={"file_ids": []},
    )
    session.add_all([run, resource])
    session.commit()
    context = AgentRuntimeContext(
        workspace_id=workspace.id,
        task_id=None,
        run_id=run.id,
        allowed_tools=("list_workspace_files",),
        tool_definitions=(
            AgentRuntimeToolDefinition(
                name="list_workspace_files",
                source="product",
                description="List authorized files.",
                input_schema={
                    "type": "object",
                    "properties": {"limit": {"type": "integer", "maximum": 10}},
                    "additionalProperties": False,
                },
                parameters={"limit": 5},
                locked_parameters=("limit",),
                required_resource_type="file_collection",
                required_access_modes=("read", "read_write"),
            ),
        ),
        resource_grants=(
            AgentRuntimeResourceGrant(
                resource_id=resource.id,
                resource_type="file_collection",
                access_mode="read",
                locator={"file_ids": []},
                parameters={},
                version=1,
            ),
        ),
    )
    gateway = AgentToolGateway(session)

    prepared = gateway.prepare(context=context, tool_name="list_workspace_files", arguments={})
    assert prepared.arguments == {"limit": 5}

    try:
        gateway.prepare(
            context=context,
            tool_name="list_workspace_files",
            arguments={"limit": 8},
        )
    except ToolGatewayDenied as exc:
        assert exc.code == "tool_parameter_locked"
    else:
        raise AssertionError("Expected a locked parameter override to be denied")

    resource.status = "disabled"
    session.commit()
    try:
        gateway.prepare(context=context, tool_name="list_workspace_files", arguments={})
    except ToolGatewayDenied as exc:
        assert exc.code == "capability_resource_disabled"
    else:
        raise AssertionError("Expected disabled resource to be denied at execution time")


def test_backend_tool_executor_reads_scoped_file_and_records_denial_evidence(
    tmp_path: Path,
) -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    allowed_file = _workspace_file(workspace, "allowed.txt", user)
    blocked_file = _workspace_file(workspace, "blocked.txt", user)
    oversized_file = _workspace_file(workspace, "oversized.txt", user)
    oversized_file.size_bytes = 5
    oversized_file.checksum_sha256 = sha256(b"large").hexdigest()
    run = AgentRun(workspace_id=workspace.id)
    resource = CapabilityResource(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        key="one-file",
        name="One file",
        resource_type="file_collection",
        access_mode="read",
        locator={},
    )
    session.add_all([allowed_file, blocked_file, oversized_file, run, resource])
    session.flush()
    resource.locator = {"file_ids": [str(allowed_file.id), str(oversized_file.id)]}
    session.commit()
    grant = AgentRuntimeResourceGrant(
        resource_id=resource.id,
        resource_type="file_collection",
        access_mode="read",
        locator=dict(resource.locator),
        parameters={},
        version=1,
    )
    definitions = (
        _file_tool("list_workspace_files"),
        _file_tool("read_workspace_file"),
    )
    context = AgentRuntimeContext(
        workspace_id=workspace.id,
        task_id=None,
        run_id=run.id,
        user_id=user.id,
        allowed_tools=("list_workspace_files", "read_workspace_file"),
        tool_definitions=definitions,
        resource_grants=(grant,),
        file_scope_ids=(allowed_file.id, blocked_file.id, oversized_file.id),
        metadata={
            "authorization_snapshot_fingerprint": "sha256:authorization",
            "capability_catalog_fingerprint": "sha256:catalog",
        },
    )
    storage = LocalStorage(str(tmp_path / "storage"))
    storage.write(allowed_file.storage_key, b"safe")
    storage.write(blocked_file.storage_key, b"safe")
    storage.write(oversized_file.storage_key, b"large")
    executor = BackendToolExecutor.for_mcp_adapter(
        session,
        _UnusedMcpAdapter(),
        settings=Settings(environment="test", agent_file_read_max_bytes=4),
        storage=storage,
    )

    listed = executor.execute_tool(
        context=context,
        tool_name="list_workspace_files",
        arguments={},
    )
    read = executor.execute_tool(
        context=context,
        tool_name="read_workspace_file",
        arguments={"file_id": str(allowed_file.id)},
    )
    denied = executor.execute_tool(
        context=context,
        tool_name="read_workspace_file",
        arguments={"file_id": str(blocked_file.id)},
    )
    oversized = executor.execute_tool(
        context=context,
        tool_name="read_workspace_file",
        arguments={"file_id": str(oversized_file.id)},
    )

    assert listed.status == "completed"
    assert listed.output is not None
    assert {item["id"] for item in listed.output["items"]} == {
        str(allowed_file.id),
        str(oversized_file.id),
    }
    assert read.status == "completed"
    assert read.output is not None
    assert read.output["content"] == "safe"
    assert read.output["encoding"] == "utf-8"
    assert read.output["checksum_verified"] is True
    assert read.output["trust_level"] == "untrusted_workspace_input"
    assert denied.status == "failed"
    assert denied.error is not None
    assert denied.error["code"] == "workspace_file_not_in_resource_scope"
    assert oversized.status == "failed"
    assert oversized.error is not None
    assert oversized.error["code"] == "workspace_file_too_large"
    security_event = session.scalar(
        select(SecurityEvent).where(
            SecurityEvent.reason == "workspace_file_not_in_resource_scope"
        )
    )
    oversized_security_event = session.scalar(
        select(SecurityEvent).where(SecurityEvent.reason == "workspace_file_too_large")
    )
    blocked_events = session.scalars(
        select(RunEvent).where(RunEvent.event_type == "tool.blocked")
    ).all()
    blocked_event = next(
        event
        for event in blocked_events
        if event.event_metadata["reason"] == "workspace_file_not_in_resource_scope"
    )
    assert security_event is not None
    assert security_event.reason == "workspace_file_not_in_resource_scope"
    assert security_event.event_metadata["capability_catalog_fingerprint"] == "sha256:catalog"
    assert oversized_security_event is not None
    assert blocked_event is not None
    assert blocked_event.event_metadata["reason"] == "workspace_file_not_in_resource_scope"


def _file_tool(name: str) -> AgentRuntimeToolDefinition:
    properties: dict[str, object] = {}
    required: list[str] = []
    if name == "read_workspace_file":
        properties["file_id"] = {"type": "string", "format": "uuid"}
        required.append("file_id")
    return AgentRuntimeToolDefinition(
        name=name,
        source="product",
        description=f"Execute {name}.",
        input_schema={
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        },
        required_resource_type="file_collection",
        required_access_modes=("read", "read_write"),
    )


def _workspace_file(workspace: Workspace, filename: str, user: User) -> WorkspaceFile:
    return WorkspaceFile(
        workspace_id=workspace.id,
        uploaded_by_user_id=user.id,
        filename=filename,
        content_type="text/plain",
        size_bytes=4,
        checksum_sha256=sha256(b"safe").hexdigest(),
        storage_key=f"workspaces/{workspace.id}/files/test/{filename}",
    )


class _UnusedMcpAdapter:
    def call(
        self,
        *,
        server: McpServer,
        tool_name: str,
        arguments: dict[str, object],
        credential_refs: list[McpCredentialReference],
        timeout_seconds: int,
    ) -> dict[str, object]:
        raise AssertionError("MCP adapter must not be called for product tools")


def _session() -> Session:
    _patch_portable_types_for_sqlite()
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


def _seed_workspace(session: Session) -> tuple[User, Workspace]:
    user = User(email=f"{uuid4()}@example.com", display_name="Owner")
    workspace = Workspace(owner=user, name="Acme", slug=str(uuid4()), settings={})
    membership = WorkspaceMember(workspace=workspace, user=user, role="owner")
    session.add_all([user, workspace, membership])
    session.commit()
    return user, workspace


def _patch_portable_types_for_sqlite() -> None:
    for table in Base.metadata.tables.values():
        for column in table.columns:
            if isinstance(column.type, PostgresUUID):
                column.type = column.type.as_generic()
            if isinstance(column.type, JSONB):
                column.type = SqliteJSON()
