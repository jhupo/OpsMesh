"""Frozen routing scope, live revocation and schema cutover against PostgreSQL."""

import asyncio
import importlib.util
import os
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from opsmesh.agents.execution.contracts import AgentExecutionContext
from opsmesh.agents.execution.providers.openai.activities import AgentExecutionActivities
from opsmesh.agents.execution.providers.openai.tools_temporal import AgentToolOperation
from opsmesh.agents.profiles.models import AgentProfile
from opsmesh.identity.authorization.execution import ExecutionIdentityService
from opsmesh.identity.users.models import User
from opsmesh.orchestration.conversations.models import (
    Conversation,
    ConversationExecution,
    ConversationTurn,
    turn_event,
)
from opsmesh.orchestration.requests.builder import RunRequestBuilder
from opsmesh.orchestration.runs.authorization.snapshot import RunAuthorizationSnapshotService
from opsmesh.orchestration.runs.authorization.tools import hydrate_agent_targets
from opsmesh.orchestration.runs.models import AgentRun
from opsmesh.orchestration.tasks.models import Task
from opsmesh.shared.config import Settings
from opsmesh.shared.db.base import Base
from opsmesh.workspaces.management.models import Workspace
from opsmesh.workspaces.members.models import WorkspaceMember
from tests.fixtures.agent_product import _seed_workspace
from tests.fixtures.database import temporary_postgres_schema
from tests.test_cost_accounting import _assert_native_graph_costs

pytestmark = pytest.mark.skipif(
    not os.getenv("OPSMESH_TEST_POSTGRES_URL"), reason="PostgreSQL required"
)


def policy(*ids, depth=1):
    return {
        "enabled": True,
        "allowed_agent_profile_ids": [str(item) for item in ids],
        "max_depth": depth,
    }


def test_graph_cost_records_are_idempotent_in_postgres_and_downgrade_preserves_evidence():
    spec = importlib.util.spec_from_file_location(
        "routing_cost_migration", Path("migrations/versions/0122_native_agent_routing.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with temporary_postgres_schema() as engine:
        Base.metadata.create_all(engine)
        with sessionmaker(engine, expire_on_commit=False)() as session:
            _assert_native_graph_costs(session)
        with (
            pytest.raises(Exception, match="split model usage records"),
            engine.begin() as connection,
            Operations.context(MigrationContext.configure(connection)),
        ):
            module.downgrade()


def test_router_graph_freezes_independent_target_scopes_and_rechecks_live_profile():
    with temporary_postgres_schema() as engine:
        Base.metadata.create_all(engine)
        factory = sessionmaker(engine, expire_on_commit=False)
        settings = Settings(environment="test")
        with factory() as session:
            user, workspace = _seed_workspace(session)
            expert = AgentProfile(
                workspace_id=workspace.id,
                name="Expert",
                role="expert",
                instructions="Analyze",
                model="gpt-4.1",
                tool_policy={"allowed_tools": ["get_agent_inbox"]},
            )
            manager = AgentProfile(
                workspace_id=workspace.id,
                name="Manager",
                role="manager",
                instructions="Synthesize",
                model="gpt-4.1",
                tool_policy={"allowed_tools": ["get_agent_inbox"]},
            )
            router = AgentProfile(
                workspace_id=workspace.id,
                name="Router",
                role="router",
                instructions="Choose",
                model="gpt-4.1-mini",
                tool_policy={"allowed_tools": []},
            )
            session.add_all([expert, manager, router])
            session.flush()
            manager.tool_policy = {**manager.tool_policy, "agent_tools": policy(expert.id)}
            router.tool_policy = {
                "allowed_tools": [],
                "handoffs": {**policy(expert.id, manager.id, depth=2), "require_handoff": True},
            }
            task = Task(
                workspace_id=workspace.id,
                created_by_user_id=user.id,
                title="Route",
                owner_agent_profile_id=router.id,
                execution_identity=ExecutionIdentityService(session).capture(workspace.id, user.id),
            )
            session.add(task)
            session.flush()
            builder = RunRequestBuilder(session, settings)
            frozen = RunAuthorizationSnapshotService(session, builder).build_authorization_snapshot(
                task, None, router
            )
            assert frozen["allowed_tools"] == [] and frozen["require_handoff"] is True
            assert frozen["max_turns"] == 8
            run = AgentRun(
                workspace_id=workspace.id,
                task_id=task.id,
                agent_profile_id=router.id,
                status="running",
                input={"authorization_snapshot": frozen},
            )
            session.add(run)
            session.commit()
            root = AgentExecutionContext(workspace.id, task.id, run.id, user_id=user.id)
            graph = hydrate_agent_targets(
                session=session,
                snapshot=frozen,
                root_context=root,
                resolve_model_provider=builder.resolve_model_provider,
                kind="handoffs",
            )
            direct = next(item for item in graph if item.target.ref.profile_id == expert.id)
            synthesis = next(item for item in graph if item.target.ref.profile_id == manager.id)
            assert direct.context.allowed_tools == ("get_agent_inbox",)
            assert synthesis.nested_tools[0].context.allowed_tools == ("get_agent_inbox",)
            assert direct.context.metadata["agent_profile_id"] == str(expert.id)
            assert direct.model == expert.model
            assert direct.model_provider_credential_id is not None
            expert.instructions = "Changed after admission"
            session.commit()
            rebuilt = hydrate_agent_targets(
                session=session,
                snapshot=frozen,
                root_context=root,
                resolve_model_provider=builder.resolve_model_provider,
                kind="handoffs",
            )
            assert rebuilt[0].target.instructions == "Analyze"
            expert.status = "archived"
            session.commit()
            with pytest.raises(ValueError, match="no longer active"):
                hydrate_agent_targets(
                    session=session,
                    snapshot=frozen,
                    root_context=root,
                    resolve_model_provider=builder.resolve_model_provider,
                    kind="handoffs",
                )

        async def authorize():
            executor = AgentExecutionActivities(factory, settings)
            with pytest.raises(ValueError, match="no longer active"):
                await executor.authorize(AgentToolOperation(context=direct.context))

        asyncio.run(authorize())


def test_schema_cutover_requires_drain_and_preserves_old_execution_evidence():
    spec = importlib.util.spec_from_file_location(
        "routing_migration", Path("migrations/versions/0122_native_agent_routing.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with temporary_postgres_schema() as engine:
        Base.metadata.create_all(engine)
        factory = sessionmaker(engine, expire_on_commit=False)
        wid, uid, _, cid = _seed_conversation(factory)
        with factory() as session:
            turn = ConversationTurn(
                workspace_id=wid,
                conversation_id=cid,
                sequence=1,
                idempotency_key="legacy",
                body="Question",
                execution_identity={},
                status="completed",
            )
            tasks = [
                Task(
                    workspace_id=wid,
                    created_by_user_id=uid,
                    title=name,
                    status="completed",
                    execution_identity={},
                )
                for name in ("manager", "delegate")
            ]
            session.add_all([turn, *tasks])
            session.flush()
            session.add(turn_event(turn))
            link = ConversationExecution(workspace_id=wid, turn_id=turn.id, task_id=tasks[0].id)
            session.add(link)
            session.commit()
            turn_id, task_ids, link_id = turn.id, [task.id for task in tasks], link.id
        child_id = uuid4()
        with (
            engine.begin() as connection,
            Operations.context(MigrationContext.configure(connection)),
        ):
            module.downgrade()
            connection.execute(text("UPDATE conversation_turns SET round=5"))
            connection.execute(text("UPDATE conversation_events SET round=3"))
            connection.execute(
                text(
                    "INSERT INTO conversation_executions"
                    "(id, workspace_id, turn_id, task_id, key, purpose, round) "
                    "VALUES (:id,:wid,:turn,:task,'delegate:1','delegate',0)"
                ),
                {"id": child_id, "wid": wid, "turn": turn_id, "task": task_ids[1]},
            )
        with engine.begin() as connection:
            connection.execute(text("UPDATE conversation_turns SET status='running'"))
        with (
            pytest.raises(Exception, match="Drain or cancel"),
            engine.begin() as connection,
            Operations.context(MigrationContext.configure(connection)),
        ):
            module.upgrade()
        with (
            engine.begin() as connection,
            Operations.context(MigrationContext.configure(connection)),
        ):
            connection.execute(text("UPDATE conversation_turns SET status='completed'"))
            module.upgrade()
            assert connection.scalar(text("SELECT count(*) FROM conversation_executions")) == 1
            assert connection.scalar(text("SELECT id FROM conversation_executions")) == link_id
            assert (
                connection.scalar(
                    text("SELECT count(*) FROM tasks WHERE input ? '_conversation_execution_0122'")
                )
                == 2
            )
            module.downgrade()
            assert connection.scalar(text("SELECT round FROM conversation_turns")) == 5
            assert connection.scalar(text("SELECT round FROM conversation_events")) == 3
            assert set(connection.scalars(text("SELECT id FROM conversation_executions"))) == {
                link_id,
                child_id,
            }
            module.upgrade()


def _seed_conversation(factory):
    with factory() as session:
        user = User(email=f"{uuid4()}@example.com", display_name="Owner")
        workspace = Workspace(owner=user, name="Test", slug=uuid4().hex, settings={})
        member = WorkspaceMember(workspace=workspace, user=user, role="owner")
        session.add_all([user, workspace, member])
        session.flush()
        agent = AgentProfile(workspace_id=workspace.id, name="Assistant", role="assistant")
        session.add(agent)
        session.flush()
        conversation = Conversation(
            workspace_id=workspace.id,
            created_by_user_id=user.id,
            title="Test",
            mode="agent",
            agent_profile_id=agent.id,
        )
        session.add(conversation)
        session.commit()
        return workspace.id, user.id, member.id, conversation.id
