from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from opsmesh.agents.execution.contracts import (
    AgentExecutionContext,
    AgentResourceGrant,
    AgentToolSpec,
)
from opsmesh.agents.execution.tools.executor import AgentToolExecutionService
from opsmesh.agents.messages.models import AgentMessage, AgentMessageThread
from opsmesh.agents.profiles.models import AgentProfile
from opsmesh.capabilities.catalog.effective import effective_catalog_fingerprint
from opsmesh.capabilities.catalog.product_tools import PRODUCT_TOOL_CATALOG
from opsmesh.capabilities.mcp.models import (
    MCPCredentialReference,
    MCPServer,
    MCPToolAllowlist,
)
from opsmesh.capabilities.references.models import CapabilityResource
from opsmesh.identity.authorization.execution import ExecutionIdentityService
from opsmesh.identity.users.models import User
from opsmesh.orchestration.runs.models import AgentRun, authorization_snapshot_fingerprint
from opsmesh.orchestration.runs.state import RunStatus
from opsmesh.orchestration.tasks.models import Task
from opsmesh.resources.memory.models import WorkspaceMemoryEntry, memory_content_fingerprint
from opsmesh.shared.db.base import Base
from opsmesh.workspaces.management.models import Workspace
from opsmesh.workspaces.members.models import WorkspaceMember
from tests.fixtures.database import product_engine
from tests.fixtures.tools import execute_tool


def test_backend_tool_executor_enforces_runtime_allowed_tools() -> None:
    session = _session()
    _, workspace = _seed_workspace(session)
    task = Task(
        execution_identity=ExecutionIdentityService(session).capture(
            workspace.id, workspace.owner_user_id
        ),
        created_by_user_id=workspace.owner_user_id,
        workspace_id=workspace.id,
        title="Task",
    )
    server = MCPServer(workspace_id=workspace.id, name="image-tools")
    session.add_all([task, server])
    session.flush()
    allow = MCPToolAllowlist(
        workspace_id=workspace.id,
        mcp_server_id=server.id,
        tool_name="generate_image",
    )
    run = AgentRun(
        workspace_id=workspace.id,
        task_id=task.id,
        input={
            "authorization_snapshot": {
                "workspace_id": str(workspace.id),
                "allowed_tools": ["generate_image"],
            }
        },
    )
    session.add_all([allow, run])
    session.flush()
    _set_mcp_snapshot(run, workspace, server, allow)
    session.commit()

    result = execute_tool(
        AgentToolExecutionService(session),
        context=AgentExecutionContext(
            user_id=workspace.owner_user_id,
            workspace_id=workspace.id,
            task_id=task.id,
            run_id=run.id,
            allowed_tools=(),
            tool_definitions=(_mcp_definition(server, allow),),
        ),
        tool_name="generate_image",
        arguments={"prompt": "mountain"},
    )

    assert result.status == "failed"
    assert result.error is not None
    assert result.error["code"] == "tool_not_in_run_manifest"


def test_backend_tool_executor_dispatches_agent_mailbox_product_tools() -> None:
    session = _session()
    _, workspace = _seed_workspace(session)
    _, other_workspace = _seed_workspace(session)
    task = Task(
        execution_identity=ExecutionIdentityService(session).capture(
            workspace.id, workspace.owner_user_id
        ),
        created_by_user_id=workspace.owner_user_id,
        workspace_id=workspace.id,
        title="Task",
    )
    sender = AgentProfile(workspace_id=workspace.id, name="Planner", role="planner")
    recipient = AgentProfile(workspace_id=workspace.id, name="Builder", role="builder")
    other_agent = AgentProfile(
        workspace_id=other_workspace.id,
        name="Foreign",
        role="builder",
    )
    session.add_all([task, sender, recipient, other_agent])
    session.flush()
    run = AgentRun(
        workspace_id=workspace.id,
        task_id=task.id,
        agent_profile_id=sender.id,
        status=RunStatus.RUNNING.value,
        input={
            "authorization_snapshot": {
                "workspace_id": str(workspace.id),
                "allowed_tools": ["send_agent_message", "list_agent_thread_messages"],
            }
        },
    )
    session.add(run)
    session.commit()
    executor = AgentToolExecutionService(session)
    context = AgentExecutionContext(
        user_id=workspace.owner_user_id,
        workspace_id=workspace.id,
        task_id=task.id,
        run_id=run.id,
        allowed_tools=("send_agent_message", "list_agent_thread_messages"),
        metadata={"agent_profile_id": str(sender.id)},
        tool_definitions=_product_definitions(
            "send_agent_message",
            "list_agent_thread_messages",
        ),
    )

    sent = execute_tool(
        executor,
        context=context,
        tool_name="send_agent_message",
        arguments={
            "recipient_agent_profile_id": str(recipient.id),
            "subject": "Handoff",
            "body": "Please continue.",
            "payload": {"scope": "backend"},
        },
    )
    assert sent.status == "completed"
    assert sent.output is not None
    assert sent.metadata["provenance"] == "backend_tool_executor"
    assert sent.metadata["tool_kind"] == "product"
    assert sent.metadata["tool_name"] == "send_agent_message"
    listed = execute_tool(
        executor,
        context=context,
        tool_name="list_agent_thread_messages",
        arguments={"thread_id": sent.output["thread"]["id"]},
    )
    sensitive = execute_tool(
        executor,
        context=context,
        tool_name="send_agent_message",
        arguments={
            "recipient_agent_profile_id": str(recipient.id),
            "body": "Please review sensitive context.",
            "payload": {"token": "hidden", "scope": "backend"},
        },
    )
    blocked = execute_tool(
        executor,
        context=context,
        tool_name="send_agent_message",
        arguments={
            "recipient_agent_profile_id": str(other_agent.id),
            "body": "Cross workspace should fail.",
        },
    )

    stored = session.get(AgentMessage, UUID(sent.output["message"]["id"]))
    assert sent.output["message"]["payload"] == {"scope": "backend"}
    assert listed.status == "completed"
    assert listed.output is not None
    assert listed.output["total"] == 1
    assert stored.payload == {"scope": "backend"}
    assert sensitive.status == "completed"
    assert blocked.status == "failed"
    assert blocked.error is not None
    assert blocked.error["code"] == "user_resource_denied"


def test_backend_tool_executor_redacts_product_tool_failure_messages(
    monkeypatch,
) -> None:
    session = _session()
    _, workspace = _seed_workspace(session)
    recipient = AgentProfile(workspace_id=workspace.id, name="Builder", role="builder")
    task = Task(
        execution_identity=ExecutionIdentityService(session).capture(
            workspace.id, workspace.owner_user_id
        ),
        created_by_user_id=workspace.owner_user_id,
        workspace_id=workspace.id,
        title="Task",
    )
    session.add(task)
    session.flush()
    run = AgentRun(
        workspace_id=workspace.id,
        task_id=task.id,
        input={
            "authorization_snapshot": {
                "workspace_id": str(workspace.id),
                "allowed_tools": ["send_agent_message"],
            }
        },
    )
    session.add_all([recipient, task, run])
    session.commit()

    def fail_send_agent_message(*args, **kwargs):
        raise ValueError("provider rejected api_key=sk-product-tool-secret")

    monkeypatch.setattr(
        "opsmesh.capabilities.tools.mailbox.AgentMailboxProductTools.send_agent_message",
        fail_send_agent_message,
    )

    result = execute_tool(
        AgentToolExecutionService(session),
        context=AgentExecutionContext(
            user_id=workspace.owner_user_id,
            workspace_id=workspace.id,
            task_id=task.id,
            run_id=run.id,
            allowed_tools=("send_agent_message",),
            tool_definitions=_product_definitions("send_agent_message"),
        ),
        tool_name="send_agent_message",
        arguments={
            "recipient_agent_profile_id": str(recipient.id),
            "body": "Should fail before sending.",
        },
    )

    assert result.status == "failed"
    assert result.error is not None
    assert result.error["code"] == "product_tool_failed"
    assert "sk-product-tool-secret" not in result.error["message"]
    assert "[redacted]" in result.error["message"]
    assert result.metadata["provenance"] == "backend_tool_executor"
    assert result.metadata["tool_kind"] == "product"


def test_backend_tool_executor_dispatches_agent_inbox_product_tools() -> None:
    session = _session()
    _, workspace = _seed_workspace(session)
    task = Task(
        execution_identity=ExecutionIdentityService(session).capture(
            workspace.id, workspace.owner_user_id
        ),
        created_by_user_id=workspace.owner_user_id,
        workspace_id=workspace.id,
        title="Task",
    )
    sender = AgentProfile(workspace_id=workspace.id, name="Planner", role="planner")
    recipient = AgentProfile(workspace_id=workspace.id, name="Builder", role="builder")
    session.add_all([task, sender, recipient])
    session.flush()
    thread = AgentMessageThread(workspace_id=workspace.id, task_id=task.id, subject="Inbox")
    session.add(thread)
    session.flush()
    message = AgentMessage(
        workspace_id=workspace.id,
        task_id=task.id,
        thread_id=thread.id,
        sender_agent_profile_id=sender.id,
        recipient_agent_profile_id=recipient.id,
        message_type="handoff",
        body="Please pick this up.",
        payload={"token": "hidden-inbox-token"},
    )
    run = AgentRun(
        workspace_id=workspace.id,
        task_id=task.id,
        agent_profile_id=recipient.id,
        input={
            "authorization_snapshot": {
                "workspace_id": str(workspace.id),
                "allowed_tools": ["get_agent_inbox", "mark_agent_message_read"],
            }
        },
    )
    session.add_all([message, run])
    session.commit()
    executor = AgentToolExecutionService(session)
    context = AgentExecutionContext(
        user_id=workspace.owner_user_id,
        workspace_id=workspace.id,
        task_id=task.id,
        run_id=run.id,
        allowed_tools=("get_agent_inbox", "mark_agent_message_read"),
        metadata={"agent_profile_id": str(recipient.id)},
        tool_definitions=_product_definitions(
            "get_agent_inbox",
            "mark_agent_message_read",
        ),
    )

    inbox = execute_tool(
        executor,
        context=context,
        tool_name="get_agent_inbox",
        arguments={"latest_limit": 5, "unread_only": True},
    )
    marked = execute_tool(
        executor,
        context=context,
        tool_name="mark_agent_message_read",
        arguments={"message_id": str(message.id)},
    )

    session.refresh(message)
    assert inbox.status == "completed"
    assert inbox.output is not None
    assert inbox.output["unread_count"] == 1
    assert inbox.output["latest_messages"][0]["payload"] == {"token": "[redacted]"}
    assert marked.status == "completed"
    assert marked.output is not None
    assert marked.output["message"]["status"] == "read"
    assert message.status == "read"
    assert message.read_at is not None


def test_backend_tool_executor_scopes_agent_inbox_to_runtime_metadata() -> None:
    session = _session()
    _, workspace = _seed_workspace(session)
    current_task = Task(
        execution_identity=ExecutionIdentityService(session).capture(
            workspace.id, workspace.owner_user_id
        ),
        created_by_user_id=workspace.owner_user_id,
        workspace_id=workspace.id,
        title="Current task",
    )
    other_task = Task(
        execution_identity=ExecutionIdentityService(session).capture(
            workspace.id, workspace.owner_user_id
        ),
        created_by_user_id=workspace.owner_user_id,
        workspace_id=workspace.id,
        title="Other task",
    )
    sender = AgentProfile(workspace_id=workspace.id, name="Planner", role="planner")
    recipient = AgentProfile(workspace_id=workspace.id, name="Builder", role="builder")
    session.add_all([current_task, other_task, sender, recipient])
    session.flush()
    current_thread = AgentMessageThread(
        workspace_id=workspace.id,
        task_id=current_task.id,
        subject="Current inbox",
    )
    other_thread = AgentMessageThread(
        workspace_id=workspace.id,
        task_id=other_task.id,
        subject="Other inbox",
    )
    session.add_all([current_thread, other_thread])
    session.flush()
    current_message = AgentMessage(
        workspace_id=workspace.id,
        task_id=current_task.id,
        thread_id=current_thread.id,
        sender_agent_profile_id=sender.id,
        recipient_agent_profile_id=recipient.id,
        message_type="handoff",
        body="Current scoped inbox message.",
    )
    other_message = AgentMessage(
        workspace_id=workspace.id,
        task_id=other_task.id,
        thread_id=other_thread.id,
        sender_agent_profile_id=sender.id,
        recipient_agent_profile_id=recipient.id,
        message_type="handoff",
        body="Other task message should not appear.",
    )
    run = AgentRun(
        workspace_id=workspace.id,
        task_id=current_task.id,
        agent_profile_id=recipient.id,
        input={
            "authorization_snapshot": {
                "workspace_id": str(workspace.id),
                "allowed_tools": ["get_agent_inbox"],
            }
        },
    )
    session.add_all([current_message, other_message, run])
    session.commit()

    result = execute_tool(
        AgentToolExecutionService(session),
        context=AgentExecutionContext(
            user_id=workspace.owner_user_id,
            workspace_id=workspace.id,
            task_id=current_task.id,
            run_id=run.id,
            allowed_tools=("get_agent_inbox",),
            tool_definitions=_product_definitions("get_agent_inbox"),
            metadata={
                "agent_profile_id": str(recipient.id),
                "agent_mailbox": {"scope": {"task_id": str(current_task.id)}},
            },
        ),
        tool_name="get_agent_inbox",
        arguments={"latest_limit": 5, "unread_only": True},
    )

    assert result.status == "completed"
    assert result.output is not None
    assert result.output["thread_count"] == 1
    assert result.output["message_count"] == 1
    assert result.output["unread_count"] == 1
    assert result.output["latest_messages"][0]["id"] == str(current_message.id)
    serialized_output = str(result.output)
    assert str(other_message.id) not in serialized_output
    assert "Other task message" not in serialized_output


def test_backend_tool_executor_scopes_mark_read_to_runtime_metadata() -> None:
    session = _session()
    _, workspace = _seed_workspace(session)
    current_task = Task(
        execution_identity=ExecutionIdentityService(session).capture(
            workspace.id, workspace.owner_user_id
        ),
        created_by_user_id=workspace.owner_user_id,
        workspace_id=workspace.id,
        title="Current task",
    )
    other_task = Task(
        execution_identity=ExecutionIdentityService(session).capture(
            workspace.id, workspace.owner_user_id
        ),
        created_by_user_id=workspace.owner_user_id,
        workspace_id=workspace.id,
        title="Other task",
    )
    sender = AgentProfile(workspace_id=workspace.id, name="Planner", role="planner")
    recipient = AgentProfile(workspace_id=workspace.id, name="Builder", role="builder")
    session.add_all([current_task, other_task, sender, recipient])
    session.flush()
    current_thread = AgentMessageThread(
        workspace_id=workspace.id,
        task_id=current_task.id,
        subject="Current inbox",
    )
    other_thread = AgentMessageThread(
        workspace_id=workspace.id,
        task_id=other_task.id,
        subject="Other inbox",
    )
    session.add_all([current_thread, other_thread])
    session.flush()
    other_message = AgentMessage(
        workspace_id=workspace.id,
        task_id=other_task.id,
        thread_id=other_thread.id,
        sender_agent_profile_id=sender.id,
        recipient_agent_profile_id=recipient.id,
        message_type="handoff",
        body="Other task message should not be marked read.",
    )
    run = AgentRun(
        workspace_id=workspace.id,
        task_id=current_task.id,
        agent_profile_id=recipient.id,
        input={
            "authorization_snapshot": {
                "workspace_id": str(workspace.id),
                "allowed_tools": ["mark_agent_message_read"],
            }
        },
    )
    session.add_all([other_message, run])
    session.commit()

    result = execute_tool(
        AgentToolExecutionService(session),
        context=AgentExecutionContext(
            user_id=workspace.owner_user_id,
            workspace_id=workspace.id,
            task_id=current_task.id,
            run_id=run.id,
            allowed_tools=("mark_agent_message_read",),
            tool_definitions=_product_definitions("mark_agent_message_read"),
            metadata={"agent_mailbox": {"scope": {"task_id": str(current_task.id)}}},
        ),
        tool_name="mark_agent_message_read",
        arguments={"message_id": str(other_message.id)},
    )

    session.refresh(other_message)
    assert result.status == "failed"
    assert result.error is not None
    assert result.error["code"] == "product_tool_failed"
    assert other_message.status == "sent"
    assert other_message.read_at is None


def test_backend_tool_executor_dispatches_workspace_memory_product_tools() -> None:
    session = _session()
    _, workspace = _seed_workspace(session)
    task = Task(
        execution_identity=ExecutionIdentityService(session).capture(
            workspace.id, workspace.owner_user_id
        ),
        created_by_user_id=workspace.owner_user_id,
        workspace_id=workspace.id,
        title="Task",
    )
    agent = AgentProfile(workspace_id=workspace.id, name="Researcher", role="researcher")
    existing_content = "The company positioning is durable multi-agent operations."
    existing = WorkspaceMemoryEntry(
        workspace_id=workspace.id,
        memory_layer="semantic",
        scope_type="workspace",
        scope_id=str(workspace.id),
        entry_type="note",
        title="Launch positioning",
        content=existing_content,
        content_fingerprint=memory_content_fingerprint("Launch positioning", existing_content),
        tags=["strategy"],
        visibility_scope="workspace",
        importance=3,
        status="active",
    )
    private_content = "multi-agent operations private agent note"
    private_agent_memory = WorkspaceMemoryEntry(
        workspace_id=workspace.id,
        memory_layer="semantic",
        scope_type="agent",
        scope_id=str(agent.id),
        entry_type="semantic_fact",
        title="Private agent note",
        content=private_content,
        content_fingerprint=memory_content_fingerprint("Private agent note", private_content),
        tags=["strategy"],
        visibility_scope="agent",
        importance=100,
        status="active",
    )
    session.add_all([task, agent, existing, private_agent_memory])
    session.flush()
    memory_grant = _memory_resource_grant(session, workspace)
    run = AgentRun(
        workspace_id=workspace.id,
        task_id=task.id,
        agent_profile_id=agent.id,
        input={
            "authorization_snapshot": {
                "workspace_id": str(workspace.id),
                "allowed_tools": [
                    "search_workspace_memory",
                    "upsert_semantic_memory",
                    "archive_semantic_memory",
                ],
            }
        },
    )
    session.add(run)
    session.commit()
    executor = AgentToolExecutionService(session)
    context = AgentExecutionContext(
        user_id=workspace.owner_user_id,
        workspace_id=workspace.id,
        task_id=task.id,
        run_id=run.id,
        allowed_tools=(
            "search_workspace_memory",
            "upsert_semantic_memory",
            "archive_semantic_memory",
        ),
        tool_definitions=_product_definitions(
            "search_workspace_memory",
            "upsert_semantic_memory",
            "archive_semantic_memory",
        ),
        resource_grants=(memory_grant,),
        metadata={"agent_profile_id": str(agent.id)},
    )

    searched = execute_tool(
        executor,
        context=context,
        tool_name="search_workspace_memory",
        arguments={"query": "multi-agent operations", "limit": 5},
    )
    remembered = execute_tool(
        executor,
        context=context,
        tool_name="upsert_semantic_memory",
        arguments={
            "scope_type": "workspace",
            "scope_id": str(workspace.id),
            "memory_key": "runtime-lesson",
            "knowledge_type": "procedure",
            "title": "Runtime lesson",
            "content": "Use persistent sessions for team operations.",
            "tags": ["runtime", "team"],
            "importance": 70,
        },
        approval_granted=True,
    )
    assert remembered.output is not None
    archived = execute_tool(
        executor,
        context=context,
        tool_name="archive_semantic_memory",
        arguments={
            "memory_entry_id": remembered.output["id"],
            "expected_revision": 1,
        },
        approval_granted=True,
    )
    denied = execute_tool(
        executor,
        context=context,
        tool_name="upsert_semantic_memory",
        arguments={
            "scope_type": "agent",
            "scope_id": str(agent.id),
            "memory_key": "unauthorized-agent-memory",
            "knowledge_type": "fact",
            "title": "Unauthorized",
            "content": "Must not be written.",
        },
        approval_granted=True,
    )

    stored = session.get(WorkspaceMemoryEntry, UUID(str(remembered.output["id"])))
    assert searched.status == "completed"
    assert searched.output is not None
    assert searched.output["items"][0]["title"] == "Launch positioning"
    assert all(item["title"] != "Private agent note" for item in searched.output["items"])
    assert remembered.status == "completed"
    assert remembered.metadata["provenance"] == "backend_tool_executor"
    assert remembered.metadata["tool_kind"] == "product"
    assert remembered.output["entry_type"] == "semantic_procedure"
    assert archived.status == "completed"
    assert archived.output is not None
    assert archived.output["status"] == "archived"
    assert denied.status == "failed"
    assert denied.error is not None
    assert denied.error["code"] == "memory_write_not_in_resource_scope"
    assert stored is not None
    assert stored.created_by_agent_run_id == run.id
    assert stored.created_by_agent_profile_id == agent.id
    assert stored.status == "archived"
    working_results = session.scalars(
        select(WorkspaceMemoryEntry).where(
            WorkspaceMemoryEntry.workspace_id == workspace.id,
            WorkspaceMemoryEntry.memory_layer == "working",
            WorkspaceMemoryEntry.scope_id == str(run.id),
            WorkspaceMemoryEntry.entry_type == "tool_result",
        )
    ).all()
    assert working_results == []


def _product_definitions(*names: str) -> tuple[AgentToolSpec, ...]:
    definitions = {item.name: item for item in PRODUCT_TOOL_CATALOG}
    return tuple(
        AgentToolSpec(
            name=name,
            source="product",
            description=definitions[name].description,
            input_schema=definitions[name].input_schema,
            requires_approval=definitions[name].requires_approval,
            risk_level=definitions[name].risk_level,
            required_resource_type=definitions[name].required_resource_type,
            required_access_modes=definitions[name].required_access_modes,
        )
        for name in names
    )


def _mcp_definition(
    server: MCPServer,
    allow: MCPToolAllowlist,
    *,
    credentials: tuple[MCPCredentialReference, ...] = (),
) -> AgentToolSpec:
    return AgentToolSpec(
        name=allow.tool_name,
        source="mcp",
        description=allow.description,
        input_schema=allow.input_schema,
        requires_approval=allow.requires_approval,
        risk_level=allow.risk_level,
        mcp_server_id=server.id,
        mcp_tool_allowlist_id=allow.id,
    )


def _set_mcp_snapshot(
    run: AgentRun,
    workspace: Workspace,
    server: MCPServer,
    allow: MCPToolAllowlist,
    *,
    credentials: tuple[MCPCredentialReference, ...] = (),
) -> None:
    server.health_status = "healthy"
    server.last_health_check_at = datetime.now(UTC)
    server.connection = {**server.connection, "requires_credentials": bool(credentials)}
    catalog: dict[str, object] = {
        "catalog_version": 1,
        "workspace_id": str(workspace.id),
        "agent_profile_id": str(run.agent_profile_id or uuid4()),
        "agent_profile_version": 1,
        "team_id": None,
        "team_policy_version": None,
        "team_member_id": None,
        "department": None,
        "tools": [
            {
                "descriptor": {
                    "name": allow.tool_name,
                    "source": "mcp",
                    "description": allow.description,
                    "input_schema": allow.input_schema,
                    "requires_approval": allow.requires_approval,
                    "risk_level": allow.risk_level,
                    "capability_key": allow.capability_key,
                    "mcp_server_id": str(server.id),
                    "mcp_tool_allowlist_id": str(allow.id),
                    "mcp_server_name": server.name,
                    "mcp_server_type": server.server_type,
                    "mcp_server_configuration_version": server.configuration_version,
                    "mcp_tool_configuration_version": allow.configuration_version,
                    "mcp_requires_credentials": bool(credentials),
                    "mcp_credential_references": [
                        {
                            "credential_reference_id": str(item.id),
                            "mcp_server_id": str(server.id),
                            "configuration_version": item.configuration_version,
                            "provider": item.provider,
                            "scopes": item.scopes,
                            "secret_fingerprint": item.secret_fingerprint,
                            "encryption_key_id": item.encryption_key_id,
                        }
                        for item in credentials
                    ],
                    "mcp_blocked_reasons": [],
                    "policy": allow.policy,
                    "required_resource_type": None,
                    "required_access_modes": [],
                },
                "parameters": {},
                "locked_parameters": [],
                "provenance": [],
            }
        ],
        "resources": [],
        "denied": [],
    }
    catalog["fingerprint"] = effective_catalog_fingerprint(catalog)
    snapshot: dict[str, object] = {
        "version": 4,
        "workspace_id": str(workspace.id),
        "allowed_tools": [allow.tool_name],
        "capability_catalog": catalog,
    }
    snapshot["fingerprint"] = authorization_snapshot_fingerprint(snapshot)
    run.input = {"authorization_snapshot": snapshot}


def _memory_resource_grant(
    session: Session,
    workspace: Workspace,
) -> AgentResourceGrant:
    resource = CapabilityResource(
        workspace_id=workspace.id,
        key=f"memory-{uuid4()}",
        name="Workspace memory",
        resource_type="memory_collection",
        access_mode="read_write",
        locator={"scope_types": ["workspace"], "scope_ids": [str(workspace.id)]},
    )
    session.add(resource)
    session.flush()
    return AgentResourceGrant(
        resource_id=resource.id,
        resource_type=resource.resource_type,
        access_mode=resource.access_mode,
        locator={"scope_types": ["workspace"], "scope_ids": [str(workspace.id)]},
        parameters={},
        version=resource.version,
    )


def _session() -> Session:
    engine = product_engine()
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


def _seed_workspace(session: Session) -> tuple[User, Workspace]:
    user = User(email=f"{uuid4()}@example.com", display_name="Owner")
    workspace = Workspace(owner=user, name="Acme", slug=str(uuid4()), settings={})
    membership = WorkspaceMember(workspace=workspace, user=user, role="owner")
    session.add_all([user, workspace, membership])
    session.commit()
    return user, workspace
