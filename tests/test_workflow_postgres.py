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

from opsmesh.agents.profiles.models import AgentProfile
from opsmesh.identity.authorization.execution import ExecutionIdentityService
from opsmesh.identity.users.models import User
from opsmesh.orchestration.definitions.application import (
    OrchestrationDefinitionApplicationService,
)
from opsmesh.orchestration.definitions.commands import OrchestrationDefinitionCreate
from opsmesh.orchestration.definitions.contracts import WorkflowNode
from opsmesh.orchestration.definitions.models import (
    OrchestrationDefinition,
    OrchestrationRevision,
)
from opsmesh.orchestration.definitions.service import (
    OrchestrationDefinitionError,
    OrchestrationDefinitionService,
)
from opsmesh.orchestration.tasks.models import Task, TaskStep
from opsmesh.shared.db.base import Base
from opsmesh.teams.management.models import AgentTeam
from opsmesh.workspaces.management.models import Workspace
from opsmesh.workspaces.members.models import WorkspaceMember
from tests.test_postgres_scheduler_concurrency import _temporary_postgres_schema
from tests.test_worker_run_execution import _seed_default_model_provider

pytestmark = pytest.mark.skipif(
    not os.getenv("OPSMESH_TEST_POSTGRES_URL"), reason="PostgreSQL integration URL required"
)


def test_plugin_deployment_admission_recovery_service_access_and_revocation() -> None:
    from datetime import UTC, datetime, timedelta

    from fastapi.testclient import TestClient

    from opsmesh.capabilities.plugins.models import (
        PluginDeployment,
        PluginInstall,
        PluginRelease,
        PluginTrustKey,
    )
    from opsmesh.main import create_app
    from opsmesh.runtime.instances.models import RuntimeTemplate
    from opsmesh.runtime.instances.plugin_processes import PluginProcessWorker
    from opsmesh.shared.config import Settings, get_settings
    from opsmesh.shared.db.session import get_db_session
    from tests.test_capability_resources import TOKEN, _headers, _seed_workspace
    from tests.test_runtime_manager import FakeDockerClient

    class ProcessDocker(FakeDockerClient):
        def node_identity(self) -> str:
            return "test-node"

        def __init__(self):
            super().__init__()
            self.containers = {}
            self.running = set()
            self.interrupt_once = True

        def create_container(self, request):
            if request.name not in self.containers:
                self.created_requests.append(request)
                self.containers[request.name] = request.name
            if self.interrupt_once:
                self.interrupt_once = False
                raise RuntimeError("Simulated lost response after container creation")
            return self.containers[request.name]

        def start_container(self, container_id):
            self.running.add(container_id)

        def container_running(self, container_id):
            return container_id in self.running

        def remove_container(self, container_id):
            self.running.discard(container_id)
            self.containers.pop(container_id, None)
            self.removed.append(container_id)

    with _temporary_postgres_schema() as engine:
        Base.metadata.create_all(engine)
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        settings = Settings(
            environment="test",
            internal_api_token=TOKEN,
            credential_encryption_secret="test-deployment-key",
        )
        app = create_app(settings)

        def database():
            with factory() as session:
                yield session

        app.dependency_overrides[get_db_session] = database
        app.dependency_overrides[get_settings] = lambda: settings
        with factory() as session:
            user, workspace = _seed_workspace(session, "hosted@example.com", "hosted")
            other, _ = _seed_workspace(session, "foreign@example.com", "foreign")
            trust = PluginTrustKey(
                workspace_id=workspace.id, key_id="hosted", plugin_key="hosted", public_key="0" * 64
            )
            install = PluginInstall(
                workspace_id=workspace.id, plugin_key="hosted", current_version="1.0.0"
            )
            template = RuntimeTemplate(
                name="hosted",
                image="ghcr.io/example/hosted@sha256:" + "a" * 64,
                default_limits={},
                default_network_policy={"mode": "internet"},
                created_at=datetime.now(UTC),
            )
            session.add_all([trust, install, template])
            session.flush()
            session.add(
                PluginRelease(
                    workspace_id=workspace.id,
                    install_id=install.id,
                    trust_key_id=trust.id,
                    version="1.0.0",
                    package={},
                    checksum="a" * 64,
                    approved_permissions=["configuration.read"],
                )
            )
            session.commit()
        client = TestClient(app, headers=_headers(user.id))
        root = f"/api/v1/workspaces/{workspace.id}/plugins/{install.id}"
        request = {
            "expected_revision": 0,
            "template_id": str(template.id),
            "platform_url": "https://platform.example.test/api/v1/",
            "permissions": ["configuration.read"],
            "environment": {"VENDOR_SECRET": "private-test-value"},
        }
        assert (
            client.put(root + "/deployment", json=request, headers=_headers(other.id)).status_code
            == 403
        )
        accepted = client.put(root + "/deployment", json=request)
        assert accepted.status_code == 202, accepted.text
        assert "private-test-value" not in accepted.text
        assert client.put(root + "/deployment", json=request).status_code == 409
        assert (
            client.put(
                root + "/configuration", json={"expected_revision": 0, "value": {"poll_seconds": 3}}
            ).status_code
            == 200
        )
        docker = ProcessDocker()
        worker = PluginProcessWorker(factory, settings, docker)

        def reconcile():
            with factory() as session:
                item = session.scalar(
                    select(PluginDeployment).where(PluginDeployment.install_id == install.id)
                )
                item.next_check_at = datetime.now(UTC) - timedelta(seconds=1)
                session.commit()
            worker.run_once()
            return client.get(root + "/deployment").json()

        assert reconcile()["status"] == "failed"
        assert reconcile()["status"] == "running"
        assert len(docker.created_requests) == 1
        assert reconcile()["status"] == "running"
        assert len(docker.created_requests) == 1
        process = docker.created_requests[0].process
        assert process.environment["VENDOR_SECRET"] == "private-test-value"
        runtime_root = f"/api/v1/plugin-runtime/{workspace.id}/{install.id}"
        plugin_headers = {"Authorization": "Bearer " + process.environment["OPSMESH_PLUGIN_TOKEN"]}
        assert client.get(runtime_root + "/configuration", headers=plugin_headers).json() == {
            "poll_seconds": 3
        }
        with factory() as session:
            item = session.scalar(
                select(PluginDeployment).where(PluginDeployment.install_id == install.id)
            )
            assert "private-test-value" not in item.encrypted_environment
            assert "private-test-value" not in str(item.configuration)
        assert client.post(root + "/deployment/stop").status_code == 202
        assert reconcile()["status"] == "stopped"
        assert not docker.running
        assert (
            client.get(runtime_root + "/configuration", headers=plugin_headers).status_code == 403
        )
        assert (
            client.put(root + "/deployment", json={**request, "expected_revision": 2}).status_code
            == 202
        )
        assert reconcile()["status"] == "running"
        # Publisher revocation must stop the process, not only reject later HTTP calls.
        with factory() as session:
            session.get(PluginTrustKey, trust.id).status = "revoked"
            session.commit()
        assert reconcile()["status"] == "blocked"
        assert not docker.running
        client.close()


def test_concurrent_publish_and_apply_keep_one_revision_and_one_plan() -> None:
    with _temporary_postgres_schema() as engine:
        Base.metadata.create_all(engine)
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        with factory() as session:
            user = User(email=f"{uuid4()}@example.com", display_name="Owner")
            workspace = Workspace(owner=user, name="Workflow", slug=uuid4().hex)
            session.add(workspace)
            session.flush()
            session.add(WorkspaceMember(workspace=workspace, user=user, role="owner"))
            session.flush()
            _seed_default_model_provider(session, workspace_id=workspace.id, user_id=user.id)
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
                created_by_user_id=user.id,
                execution_identity=ExecutionIdentityService(session).capture(workspace.id, user.id),
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
                    OrchestrationDefinitionApplicationService(session).apply_to_task(
                        task,
                        definition_id,
                    )
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
    migration = import_module("migrations.versions.0075_workflow_revisions")
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
