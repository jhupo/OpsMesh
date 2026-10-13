"""Product tool workflows against PostgreSQL, including scope and transaction rejection."""

import os
from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

from opsmesh.agents.execution.contracts import AgentExecutionContext
from opsmesh.agents.execution.tools.executor import AgentToolExecutionService
from opsmesh.agents.profiles.models import AgentProfile
from opsmesh.identity.authorization.execution import ExecutionIdentityService
from opsmesh.identity.authorization.resource_queries import execution_resource_queries
from opsmesh.identity.users.models import User
from opsmesh.orchestration.approvals.models import Approval
from opsmesh.orchestration.approvals.pending_tools import (
    PendingToolInvocationRequest,
    PendingToolInvocationService,
)
from opsmesh.orchestration.runs.models import AgentRun
from opsmesh.orchestration.tasks.models import Task
from opsmesh.resources.knowledge.activities import KnowledgeIngestionActivities
from opsmesh.resources.knowledge.content import KnowledgeURLFetcher
from opsmesh.resources.knowledge.ingestion import KnowledgeSourceIngestionService
from opsmesh.resources.knowledge.models import KnowledgeSource, KnowledgeSourceRevision
from opsmesh.resources.memory.models import WorkspaceMemoryEntry
from opsmesh.runtime.temporal.activities import ActivityDependencies
from opsmesh.runtime.temporal.contracts import DispatchRequest, ExecutionKind, ExecutionRequest
from opsmesh.runtime.temporal.models import TemporalOutboxEntry
from opsmesh.shared.config import Settings
from opsmesh.shared.db.base import Base
from opsmesh.shared.security.secrets import SecretEncryptionService
from opsmesh.workspaces.management.models import Workspace
from opsmesh.workspaces.members.models import WorkspaceMember
from tests.fixtures.database import temporary_postgres_schema
from tests.test_agent_execution_tools import _memory_resource_grant, _product_definitions

pytestmark = pytest.mark.skipif(
    not os.getenv("OPSMESH_TEST_POSTGRES_URL"), reason="PostgreSQL URL required"
)

TOOLS = (
    "create_knowledge",
    "update_knowledge",
    "archive_knowledge",
    "get_knowledge",
    "list_knowledge",
    "search_knowledge",
    "search_workspace_memory",
    "get_knowledge_ingestion",
    "upsert_semantic_memory",
    "archive_semantic_memory",
    "get_semantic_memory",
    "list_semantic_memory",
    "list_semantic_memory_revisions",
)


def test_text_source_migration_round_trip_and_refuses_history_loss():
    with temporary_postgres_schema() as engine:
        with engine.connect() as connection:
            schema = connection.scalar(text("SELECT current_schema()"))
        url = engine.url.update_query_dict({"options": f"-csearch_path={schema},public"})
        config = Config("alembic.ini")
        config.set_main_option(
            "sqlalchemy.url", url.render_as_string(hide_password=False).replace("%", "%%")
        )
        command.upgrade(config, "head")
        factory = sessionmaker(engine, expire_on_commit=False)
        context = _seed(factory)
        created = _call(
            factory, context, "create_knowledge", {"name": "Policy", "content": "Keep evidence"}
        )
        assert created.status == "completed", created.error
        source_id = UUID(created.output["source_id"])
        # An old text revision must block downgrade even after the head is a URL.
        with factory() as session:
            source = session.get(KnowledgeSource, source_id)
            source.source_type = "url"
            source.uri = "https://example.com/policy"
            source.source_config = {}
            session.get(AgentRun, context.run_id).status = "completed"
            session.commit()
        with pytest.raises(RuntimeError, match="Drain Temporal delivery intents"):
            command.downgrade(config, "0120_retired_egress_configuration")
        # This migration fixture acknowledges its generated delivery record. Native
        # publication/idempotency is covered by the real Temporal outbox suite.
        with factory() as session:
            for dispatch in session.scalars(select(TemporalOutboxEntry)):
                dispatch.published_at = datetime.now(UTC)
            session.commit()
        with pytest.raises(DBAPIError, match="Export and remove text"):
            command.downgrade(config, "0120_retired_egress_configuration")
        with factory() as session:
            assert session.scalar(select(KnowledgeSourceRevision.source_type)) == "text"
            session.delete(session.get(KnowledgeSource, source_id))
            session.commit()
        command.downgrade(config, "0120_retired_egress_configuration")
        command.upgrade(config, "head")
        recreated = _call(
            factory, context, "create_knowledge", {"name": "Policy", "content": "Keep evidence"}
        )
        assert recreated.status == "completed", recreated.error


def _seed(factory):
    with factory() as session:
        user = User(email=f"{uuid4()}@example.com", display_name="Owner")
        workspace = Workspace(owner=user, name="Knowledge", slug=uuid4().hex)
        session.add_all(
            [user, workspace, WorkspaceMember(workspace=workspace, user=user, role="owner")]
        )
        session.flush()
        wid, uid = workspace.id, user.id
        profile = AgentProfile(workspace_id=wid, name="Knowledge Manager", role="manager")
        session.add(profile)
        session.flush()
        task = Task(
            workspace_id=wid,
            created_by_user_id=uid,
            title="Maintain knowledge",
            execution_identity=ExecutionIdentityService(session).capture(wid, uid),
        )
        session.add(task)
        session.flush()
        run = AgentRun(
            workspace_id=wid,
            task_id=task.id,
            agent_profile_id=profile.id,
            status="running",
            input={},
        )
        session.add(run)
        grant = _memory_resource_grant(session, session.get(Workspace, wid))
        session.commit()
        return AgentExecutionContext(
            workspace_id=wid,
            user_id=uid,
            task_id=task.id,
            run_id=run.id,
            allowed_tools=TOOLS,
            tool_definitions=_product_definitions(*TOOLS),
            resource_grants=(grant,),
            metadata={"agent_profile_id": str(profile.id)},
        )


def _call(factory, context, tool, arguments):
    with factory() as session:
        user = ExecutionIdentityService(session).for_run(context.workspace_id, context.run_id)
        with execution_resource_queries(session, context.workspace_id, user):
            secrets = SecretEncryptionService(secret="knowledge-acceptance-secret", key_id="test")
            executor = AgentToolExecutionService(session, secret_service=secrets)
            call_id = uuid4().hex
            if tool == "archive_knowledge":
                review = executor.review_tool_call(
                    context=context, tool_name=tool, arguments=arguments
                )
                assert review["required"]
                approval = Approval(
                    workspace_id=context.workspace_id,
                    task_id=context.task_id,
                    agent_run_id=context.run_id,
                    approval_type="product.tool",
                    risk_level="medium",
                    status="approved",
                    created_at=datetime.now(UTC),
                )
                session.add(approval)
                session.flush()
                pending = PendingToolInvocationService(session, secrets).create_or_get(
                    PendingToolInvocationRequest(
                        workspace_id=context.workspace_id,
                        task_id=context.task_id,
                        agent_run_id=context.run_id,
                        approval_id=approval.id,
                        tool_call_id=call_id,
                        tool_name=tool,
                        tool_kind="product",
                        arguments=arguments,
                        policy_decision=review,
                        idempotency_key=call_id,
                    )
                )
                pending.status = "approved"
                session.commit()
            result = executor.execute_tool(
                context=context,
                tool_name=tool,
                arguments=arguments,
                approval_granted=True,
                tool_call_id=call_id,
            )
        return result


def _ingest(factory, context, source_id, version):
    from unittest.mock import Mock

    KnowledgeIngestionActivities(
        ActivityDependencies(factory, Settings(environment="test")),
        Mock(spec=KnowledgeURLFetcher),
    ).ingest(
        ExecutionRequest(
            workspace_id=context.workspace_id,
            resource_id=source_id,
            kind=ExecutionKind.KNOWLEDGE_INGEST,
            requested_by_user_id=context.user_id,
            parameters={"source_version": version},
        )
    )
    with factory() as session:
        from opsmesh.resources.knowledge.models import KnowledgeSourceIngestion

        assert (
            session.scalar(
                select(KnowledgeSourceIngestion).where(
                    KnowledgeSourceIngestion.workspace_id == context.workspace_id,
                    KnowledgeSourceIngestion.source_id == source_id,
                    KnowledgeSourceIngestion.source_version == version,
                )
            ).status
            == "succeeded"
        )


def test_knowledge_tools_commit_source_and_job_then_reject_stale_or_foreign_writes():
    with temporary_postgres_schema() as engine:
        Base.metadata.create_all(engine)
        factory = sessionmaker(engine, expire_on_commit=False)
        context = _seed(factory)
        other = _seed(factory)
        read_only = replace(
            context,
            resource_grants=(replace(context.resource_grants[0], access_mode="read"),),
        )
        assert (
            _call(
                factory, read_only, "create_knowledge", {"name": "Denied", "content": "Denied"}
            ).status
            == "failed"
        )
        assert (
            _call(
                factory,
                context,
                "create_knowledge",
                {"name": "Denied file", "workspace_file_id": str(uuid4())},
            ).status
            == "failed"
        )
        created = _call(
            factory,
            context,
            "create_knowledge",
            {
                "name": "Refund policy",
                "content": "Refund requires verified order evidence.",
            },
        )
        assert created.status == "completed", created.error
        source_id = UUID(created.output["source_id"])
        with factory() as session:
            assert session.scalar(select(func.count()).select_from(TemporalOutboxEntry)) == 1
            source = session.get(KnowledgeSource, source_id)
            assert source.source_type == "text"
            service = KnowledgeSourceIngestionService(session)
            first = service.request(
                workspace_id=context.workspace_id,
                source_id=source_id,
                requested_by_user_id=context.user_id,
            )
            second = service.request(
                workspace_id=context.workspace_id,
                source_id=source_id,
                requested_by_user_id=context.user_id,
            )
            assert first.id == second.id
            session.commit()
            dispatches = session.scalars(select(TemporalOutboxEntry)).all()
            assert len(dispatches) == 1
            request = DispatchRequest.model_validate(dispatches[0].payload).execution
            assert request is not None
            assert request.kind is ExecutionKind.KNOWLEDGE_INGEST
            assert request.resource_id == source_id
            assert request.workspace_id == context.workspace_id
            first.status = "failed"
            session.commit()
            service.request(
                workspace_id=context.workspace_id,
                source_id=source_id,
                requested_by_user_id=context.user_id,
            )
            session.commit()
            dispatches = session.scalars(select(TemporalOutboxEntry)).all()
            assert len(dispatches) == 2
            assert all(
                row.payload["execution"]["resource_id"] == str(source_id) for row in dispatches
            )
        duplicate = _call(
            factory,
            context,
            "create_knowledge",
            {
                "name": "Refund policy",
                "content": "Duplicate must not leave a job.",
            },
        )
        assert duplicate.status == "failed"
        assert (
            _call(factory, other, "get_knowledge", {"source_id": str(source_id)}).status == "failed"
        )
        assert _call(factory, other, "list_knowledge", {}).output["total"] == 0
        _ingest(factory, context, source_id, 1)
        found = _call(factory, context, "search_knowledge", {"query": "verified order"})
        assert found.output["items"][0]["citations"]
        assert "content" not in found.output["items"][0]
        state = _call(
            factory,
            context,
            "get_knowledge_ingestion",
            {
                "source_id": str(source_id),
                "ingestion_id": created.output["ingestion_id"],
            },
        )
        assert state.output["status"] == "succeeded"
        assert state.output["vector_ready"] is False
        updated = _call(
            factory,
            context,
            "update_knowledge",
            {
                "source_id": str(source_id),
                "expected_version": 1,
                "content": "Refund requires manager approval.",
            },
        )
        assert updated.status == "completed", updated.error
        assert (
            _call(factory, context, "search_knowledge", {"query": "verified"}).output["items"] == []
        )
        stale = _call(
            factory,
            context,
            "update_knowledge",
            {
                "source_id": str(source_id),
                "expected_version": 1,
                "content": "Stale content",
            },
        )
        assert stale.status == "failed"
        page = _call(
            factory,
            context,
            "get_knowledge",
            {
                "source_id": str(source_id),
                "content_limit": 6,
            },
        )
        assert page.output["content"] == "Refund"
        assert page.output["has_more"] is True
        with factory() as session:
            assert session.scalar(select(func.count()).select_from(TemporalOutboxEntry)) == 3
            entries = session.scalars(
                select(WorkspaceMemoryEntry).where(
                    WorkspaceMemoryEntry.source_id == str(source_id),
                )
            ).all()
            assert entries and all(
                entry.status == "archived" and entry.embedding is None for entry in entries
            )
        _ingest(factory, context, source_id, 2)
        archived = _call(
            factory,
            context,
            "archive_knowledge",
            {
                "source_id": str(source_id),
                "expected_version": 2,
            },
        )
        assert archived.status == "completed", archived.error
        assert (
            _call(factory, context, "search_knowledge", {"query": "manager"}).output["items"] == []
        )
        assert _call(factory, context, "list_knowledge", {}).output["total"] == 0


def test_knowledge_rename_conflict_preserves_current_version_and_index():
    with temporary_postgres_schema() as engine:
        Base.metadata.create_all(engine)
        factory = sessionmaker(engine, expire_on_commit=False)
        context = _seed(factory)
        first = _call(
            factory, context, "create_knowledge", {"name": "First", "content": "Original evidence"}
        )
        second = _call(
            factory, context, "create_knowledge", {"name": "Second", "content": "Other evidence"}
        )
        assert first.status == second.status == "completed"
        source_id = UUID(first.output["source_id"])
        _ingest(factory, context, source_id, 1)
        renamed = _call(
            factory,
            context,
            "update_knowledge",
            {"source_id": str(source_id), "expected_version": 1, "name": "Second"},
        )
        assert renamed.status == "failed"
        current = _call(factory, context, "get_knowledge", {"source_id": str(source_id)})
        assert current.output["name"] == "First"
        assert current.output["version"] == 1
        assert _call(factory, context, "search_knowledge", {"query": "Original"}).output["items"]
        with factory() as session:
            assert session.scalar(select(func.count()).select_from(TemporalOutboxEntry)) == 2


def test_memory_revision_reads_enforce_scope_and_read_only_grants():
    with temporary_postgres_schema() as engine:
        Base.metadata.create_all(engine)
        factory = sessionmaker(engine, expire_on_commit=False)
        context = _seed(factory)
        context = replace(
            context,
            resource_grants=(
                replace(
                    context.resource_grants[0],
                    locator={
                        "source_types": ["semantic_memory", "agent_episode"],
                        "scope_types": ["workspace"],
                        "scope_ids": [str(context.workspace_id)],
                    },
                ),
            ),
        )
        other = _seed(factory)
        args = {
            "scope_id": str(context.workspace_id),
            "scope_type": "workspace",
            "memory_key": "preference",
            "title": "Preference",
            "content": "Use citations",
            "tags": ["support"],
            "expected_revision": 0,
        }
        created = _call(factory, context, "upsert_semantic_memory", args)
        assert created.status == "completed", created.error
        memory_id = created.output["id"]
        knowledge_only = replace(
            context,
            resource_grants=(
                replace(context.resource_grants[0], locator={"source_types": ["knowledge_source"]}),
            ),
        )
        assert (
            _call(
                factory,
                knowledge_only,
                "upsert_semantic_memory",
                {
                    **args,
                    "expected_revision": 1,
                    "content": "Forbidden replacement",
                },
            ).status
            == "failed"
        )
        assert (
            _call(
                factory,
                knowledge_only,
                "archive_semantic_memory",
                {
                    "memory_entry_id": memory_id,
                    "expected_revision": 1,
                },
            ).status
            == "failed"
        )
        updated = _call(
            factory,
            context,
            "upsert_semantic_memory",
            {
                **args,
                "expected_revision": 1,
                "content": "Cite original evidence",
                "tags": ["support", "audit"],
            },
        )
        assert updated.status == "completed", updated.error
        assert (
            _call(
                factory,
                context,
                "get_semantic_memory",
                {
                    "memory_entry_id": memory_id,
                    "revision": 1,
                },
            ).output["content"]
            == "Use citations"
        )
        assert (
            _call(
                factory,
                context,
                "list_semantic_memory_revisions",
                {
                    "memory_entry_id": memory_id,
                    "limit": 1,
                    "offset": 1,
                },
            ).output["items"][0]["revision"]
            == 1
        )
        assert _call(factory, context, "list_semantic_memory", {}).output["total"] == 1
        matches = _call(
            factory, context, "search_workspace_memory", {"query": "original evidence", "limit": 3}
        ).output["items"]
        assert matches and matches[0]["source_type"] == "semantic_memory"
        assert matches[0]["memory_entry_id"] == memory_id
        assert (
            _call(
                factory,
                knowledge_only,
                "search_workspace_memory",
                {"query": "original evidence", "limit": 3},
            ).output["items"]
            == []
        )
        audit_reader = replace(
            context,
            resource_grants=(
                replace(
                    context.resource_grants[0], access_mode="read", locator={"tags": ["audit"]}
                ),
            ),
        )
        assert (
            _call(
                factory, audit_reader, "get_semantic_memory", {"memory_entry_id": memory_id}
            ).status
            == "completed"
        )
        assert (
            _call(
                factory,
                audit_reader,
                "get_semantic_memory",
                {"memory_entry_id": memory_id, "revision": 1},
            ).status
            == "failed"
        )
        assert (
            _call(
                factory,
                other,
                "get_semantic_memory",
                {
                    "memory_entry_id": memory_id,
                },
            ).status
            == "failed"
        )
        restricted = replace(
            context,
            resource_grants=(
                replace(
                    context.resource_grants[0],
                    access_mode="read",
                    locator={"tags": ["other"]},
                ),
            ),
        )
        assert _call(factory, restricted, "list_semantic_memory", {}).output["total"] == 0
        assert (
            _call(
                factory,
                restricted,
                "get_semantic_memory",
                {
                    "memory_entry_id": memory_id,
                },
            ).status
            == "failed"
        )
        assert (
            _call(
                factory,
                restricted,
                "upsert_semantic_memory",
                {
                    **args,
                    "expected_revision": 2,
                },
            ).status
            == "failed"
        )
        archived = _call(
            factory,
            context,
            "archive_semantic_memory",
            {
                "memory_entry_id": memory_id,
                "expected_revision": 2,
            },
        )
        assert archived.status == "completed", archived.error
        assert _call(factory, context, "list_semantic_memory", {}).output["total"] == 0
        assert (
            _call(
                factory,
                context,
                "list_semantic_memory",
                {
                    "include_archived": True,
                },
            ).output["total"]
            == 1
        )


def test_knowledge_archive_requires_approval_before_mutation():
    with temporary_postgres_schema() as engine:
        Base.metadata.create_all(engine)
        factory = sessionmaker(engine, expire_on_commit=False)
        context = _seed(factory)
        created = _call(
            factory, context, "create_knowledge", {"name": "Temporary", "content": "Data"}
        )
        assert created.status == "completed", created.error
        arguments = {"source_id": created.output["source_id"], "expected_version": 1}
        with factory() as session:
            user = ExecutionIdentityService(session).for_run(context.workspace_id, context.run_id)
            with execution_resource_queries(session, context.workspace_id, user):
                review = AgentToolExecutionService(session).review_tool_call(
                    context=context,
                    tool_name="archive_knowledge",
                    arguments=arguments,
                )
            assert review["required"] is True and review["blocked"] is False
            source = session.get(KnowledgeSource, UUID(arguments["source_id"]))
            assert source.status == "active" and source.version == 1
        archived = _call(factory, context, "archive_knowledge", arguments)
        assert archived.status == "completed", archived.error
        assert archived.output["status"] == "archived"
