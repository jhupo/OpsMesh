"""Workflow transactions exercised against an isolated PostgreSQL schema."""

import os
from concurrent.futures import ThreadPoolExecutor
from importlib import import_module
from threading import Barrier
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from backend.app.agents.models import AgentProfile
from backend.app.db.base import Base
from backend.app.identity.models import User
from backend.app.orchestration.definition_commands import OrchestrationDefinitionCreate
from backend.app.orchestration.definitions import (
    OrchestrationDefinitionError,
    OrchestrationDefinitionService,
)
from backend.app.orchestration.models import OrchestrationDefinition, OrchestrationRevision
from backend.app.planning.workflow_contracts import WorkflowNode
from backend.app.tasks.models import Task, TaskStep
from backend.app.teams.models import AgentTeam
from backend.app.workspaces.models import Workspace
from backend.tests.test_postgres_scheduler_concurrency import _temporary_postgres_schema

pytestmark = pytest.mark.skipif(
    not os.getenv("OPSMESH_TEST_POSTGRES_URL"), reason="PostgreSQL integration URL required"
)


def test_concurrent_publish_and_apply_keep_one_revision_and_one_plan() -> None:
    with _temporary_postgres_schema() as engine:
        Base.metadata.create_all(engine)
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        with factory() as session:
            user = User(email=f"{uuid4()}@example.com", display_name="Owner")
            workspace = Workspace(owner=user, name="Workflow", slug=uuid4().hex)
            session.add(workspace)
            session.flush()
            agent = AgentProfile(workspace_id=workspace.id, name="Manager", role="manager")
            session.add(agent)
            session.flush()
            team = AgentTeam(
                workspace_id=workspace.id,
                name="Team",
                manager_agent_profile_id=agent.id,
            )
            session.add(team)
            session.flush()
            task = Task(
                workspace_id=workspace.id,
                agent_team_id=team.id,
                title="Workflow",
            )
            session.add(task)
            definition = OrchestrationDefinitionService(session).create_definition(
                workspace.id,
                OrchestrationDefinitionCreate(
                    key="workflow",
                    name="Workflow",
                    nodes=[
                        WorkflowNode(
                            package_id="work",
                            title="Work",
                            required_role="manager",
                            assigned_agent_profile_id=agent.id,
                        )
                    ],
                ),
            )
            workspace_id, task_id, definition_id = workspace.id, task.id, definition.id
        barrier = Barrier(2)

        def publish() -> None:
            with factory() as session:
                barrier.wait(timeout=10)
                OrchestrationDefinitionService(session).publish_definition(
                    workspace_id,
                    definition_id,
                )
                session.commit()

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(publish) for _ in range(2)]
            for future in futures:
                future.result(timeout=20)
        barrier = Barrier(2)

        def apply() -> str:
            with factory() as session:
                task = session.scalar(
                    select(Task).where(
                        Task.workspace_id == workspace_id,
                        Task.id == task_id,
                    )
                )
                assert task is not None
                barrier.wait(timeout=10)
                try:
                    OrchestrationDefinitionService(session).apply_to_task(task, definition_id)
                    session.commit()
                    return "applied"
                except OrchestrationDefinitionError as exc:
                    session.rollback()
                    return exc.code

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(apply) for _ in range(2)]
            outcomes = sorted(future.result(timeout=20) for future in futures)
        assert outcomes == ["applied", "orchestration_task_has_steps"]
        with factory() as session:
            assert session.scalar(select(func.count(OrchestrationRevision.id))) == 1
            assert session.scalar(select(func.count(TaskStep.id))) == 1


def test_revision_migration_preserves_published_nodes_and_skipped_evidence() -> None:
    migration = import_module("backend.migrations.versions.0075_workflow_revisions")
    with _temporary_postgres_schema() as engine:
        Base.metadata.create_all(engine)
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        with factory() as session:
            workspace = Workspace(
                owner=User(email=f"{uuid4()}@example.com", display_name="Owner"),
                name="Migration",
                slug=uuid4().hex,
            )
            session.add(workspace)
            session.flush()
            definition = OrchestrationDefinition(
                workspace_id=workspace.id,
                key="old",
                name="Old",
                status="published",
                version=3,
                definition={
                    "definition_version": 1,
                    "nodes": [
                        {
                            "node_id": "plain",
                            "title": "Plain",
                            "condition": {},
                            "mcp_tools": [],
                        }
                    ],
                },
            )
            task = Task(workspace_id=workspace.id, title="Old")
            session.add_all([definition, task])
            session.flush()
            session.add(
                TaskStep(
                    workspace_id=workspace.id,
                    task_id=task.id,
                    title="Skipped",
                    status="cancelled",
                    dependencies={"condition_result": "false"},
                )
            )
            session.commit()
        with engine.begin() as connection:
            OrchestrationRevision.__table__.drop(connection)
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
        with factory() as session:
            revision = session.scalar(select(OrchestrationRevision))
            assert revision is not None and revision.version == 3
            assert revision.definition["nodes"][0]["package_id"] == "plain"
            assert "condition" not in revision.definition["nodes"][0]
            assert session.scalar(select(TaskStep.status)) == "skipped"
        with (
            engine.begin() as connection,
            Operations.context(MigrationContext.configure(connection)),
        ):
            migration.downgrade()
        with factory() as session:
            definition = session.scalar(select(OrchestrationDefinition))
            assert definition is not None
            assert definition.definition["nodes"][0]["node_id"] == "plain"
            assert session.scalar(select(TaskStep.status)) == "cancelled"
        with (
            engine.begin() as connection,
            Operations.context(MigrationContext.configure(connection)),
        ):
            migration.upgrade()
