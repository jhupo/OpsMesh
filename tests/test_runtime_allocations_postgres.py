"""Real host admission locks, tenant ownership and migration contracts."""

import os
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from uuid import uuid4

import fakeredis
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, select, text
from sqlalchemy.orm import sessionmaker

from opsmesh.agents.profiles.models import AgentProfile, AgentProfileVersion
from opsmesh.bootstrap.models import register_models
from opsmesh.identity.users.models import User
from opsmesh.runtime.instances.allocations import RuntimeAllocationStore
from opsmesh.runtime.instances.models import RuntimeAllocation, WorkspaceRuntime
from opsmesh.runtime.queues.contracts import JobPayload, JobType
from opsmesh.runtime.workers.capacity import WorkerCapacitySnapshotService
from opsmesh.runtime.workers.dispatch_contracts import WorkerEventDispatchSummary
from opsmesh.runtime.workers.maintenance_contracts import WorkerMaintenanceSummary
from opsmesh.runtime.workers.models import WorkerNode, WorkerRunnerConfig
from opsmesh.runtime.workers.runner import WorkerRunner
from opsmesh.workspaces.management.models import Workspace
from tests.fixtures.runtime_host import runtime_host
from tests.test_postgres_scheduler_concurrency import _temporary_postgres_schema
from tests.test_redis_queue import _queue

pytestmark = pytest.mark.skipif(
    not os.getenv("OPSMESH_TEST_POSTGRES_URL"),
    reason="PostgreSQL integration URL required",
)


def test_two_worker_processes_cannot_overbook_node_resource_budget():
    with _temporary_postgres_schema() as engine:
        register_models().create_all(engine)
        factory = sessionmaker(engine, expire_on_commit=False)
        with factory() as session:
            user = User(email=f"{uuid4()}@node.test", display_name="Owner")
            workspace = Workspace(owner=user, name="Node", slug=uuid4().hex)
            session.add_all([user, workspace])
            session.flush()
            workspace_id = workspace.id
            for identity in ("one", "two"):
                session.add(
                    WorkerNode(
                        worker_id=identity,
                        status="online",
                        queue_name="agent_runs",
                        capacity={
                            "node_id": "shared-node",
                            "max_jobs": 8,
                            "cpu_count": 4,
                            "memory_mb": 8192,
                        },
                        details={},
                        last_seen_at=datetime.now(UTC),
                    )
                )
            session.commit()
        queue = _queue(fakeredis.FakeRedis(decode_responses=True))
        for _ in range(2):
            queue.enqueue(
                JobPayload(
                    workspace_id=workspace_id,
                    resource_id=uuid4(),
                    job_type=JobType.MEMORY_INDEX,
                    idempotency_key=uuid4().hex,
                    routing={"resource_requirements": {"cpu_count": 3}},
                )
            )
        barrier = threading.Barrier(2)

        def claim(identity):
            runner = WorkerRunner(
                queue=queue,
                session_factory=factory,
                config=WorkerRunnerConfig(worker_id=identity, node_id="shared-node", cpu_count=4),
                handler_factory=lambda session: None,
                maintenance=lambda: WorkerMaintenanceSummary(recovered_runs=0, expired_leases=0),
                dispatch_events=lambda: WorkerEventDispatchSummary(),
                admission_blocked=lambda session: False,
                can_claim=lambda session, job: True,
                on_job_failure=lambda job, **kwargs: None,
            )
            barrier.wait(timeout=10)
            return runner._claim()

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(claim, ("one", "two")))
        assert sum(result is not None for result in results) == 1
        assert queue.count_queued() == 1
        with factory() as session:
            snapshot = WorkerCapacitySnapshotService(session).worker_capacity_snapshot("two")
            assert snapshot.capacity["reserved_resources"] == {"cpu_count": 3}


def test_parallel_admission_is_bounded_idempotent_and_workspace_scoped():
    with _temporary_postgres_schema() as engine:
        register_models().create_all(engine)
        factory = sessionmaker(engine, expire_on_commit=False)
        with factory() as session:
            owner = User(email=f"{uuid4()}@test.com", display_name="Owner")
            workspace = Workspace(owner=owner, name="Host", slug=uuid4().hex)
            session.add_all([owner, workspace])
            session.flush()
            host = WorkspaceRuntime(
                workspace_id=workspace.id,
                name="Shared",
                execution_mode="shared",
                status="running",
                connection_status="online",
                limits={"max_concurrent_executions": 2},
                host=runtime_host(workspace.id, "real-pg-host", capacity=2),
            )
            session.add(host)
            session.commit()
            host_id, workspace_id = host.id, workspace.id

        owners = [uuid4() for _ in range(3)]
        barrier = threading.Barrier(3)

        def claim(owner_id):
            with factory() as session:
                host = session.get(WorkspaceRuntime, host_id)
                barrier.wait(timeout=10)
                allocation = RuntimeAllocationStore(session).acquire(host, "run", owner_id)
                session.commit()
                return allocation is not None

        with ThreadPoolExecutor(max_workers=3) as workers:
            results = list(workers.map(claim, owners))
        # A competing row lock is a capacity wait, never a blocked DB thread.
        assert 1 <= sum(results) <= 2
        with factory() as session:
            host = session.get(WorkspaceRuntime, host_id)
            store = RuntimeAllocationStore(session)
            for owner in owners:
                store.acquire(host, "run", owner)
            session.commit()
            allocations = session.scalars(select(RuntimeAllocation)).all()
            assert len(allocations) == 2
            first = allocations[0].owner_id
            assert store.acquire(host, "run", first).id == allocations[0].id
            with pytest.raises(ValueError, match="active task or MCP"):
                store.require_idle(host)
            store.release(host, "run", first)
            session.commit()
            assert store.acquire(host, "mcp", uuid4()) is not None
            session.commit()
            unrelated = WorkspaceRuntime(
                id=host_id,
                workspace_id=uuid4(),
                name="Unrelated",
                limits=host.limits,
                host_id=host.host_id,
            )
            assert store.get(unrelated, "run", allocations[1].owner_id) is None
            assert store.acquire(unrelated, "run", uuid4()) is None
            assert (
                len(
                    session.scalars(
                        select(RuntimeAllocation).where(
                            RuntimeAllocation.workspace_id == workspace_id
                        )
                    ).all()
                )
                == 2
            )


def test_distinct_policies_share_physical_capacity_and_cleanup_ownership():
    from opsmesh.runtime.instances.contracts import RuntimeLimits
    from opsmesh.runtime.instances.manager import RuntimeManager
    from opsmesh.runtime.instances.models import RuntimeHost, RuntimeTemplate
    from tests.test_runtime_manager import FakeDockerClient

    with _temporary_postgres_schema() as engine:
        register_models().create_all(engine)
        factory = sessionmaker(engine, expire_on_commit=False)
        docker = FakeDockerClient()
        with factory() as session:
            user = User(email=f"{uuid4()}@policy.test", display_name="Owner")
            workspace = Workspace(
                owner=user,
                name="Policies",
                slug=uuid4().hex,
                settings={"runtime_quota": {"max_active_runtimes": 1}},
            )
            template = RuntimeTemplate(
                name=uuid4().hex, image="sha256:" + "0" * 64, created_at=datetime.now(UTC)
            )
            session.add_all([user, workspace, template])
            session.commit()
            manager = RuntimeManager(session, docker)
            limits = RuntimeLimits(
                cpu_count=1,
                memory_mb=512,
                disk_mb=512,
                timeout_seconds=30,
                max_concurrent_executions=2,
            )
            denied = manager.create_runtime(
                workspace_id=workspace.id, template=template, name="Denied", limits=limits
            )
            manager.start_runtime(denied)
            allowed = manager.create_runtime(
                workspace_id=workspace.id,
                template=template,
                name="Public",
                limits=limits,
                network_disabled=False,
            )
            assert allowed.host_id == denied.host_id
            assert len(session.scalars(select(RuntimeHost)).all()) == 1
            assert len(docker.created_requests) == 1
            policy_ids = [denied.id, allowed.id]
            volume = allowed.host.resources["docker_volumes"][0]
        barrier = threading.Barrier(4)

        def claim(index):
            with factory() as session:
                policy = session.get(WorkspaceRuntime, policy_ids[index % 2])
                barrier.wait(timeout=10)
                allocation = RuntimeAllocationStore(session).acquire(policy, "run", uuid4())
                session.commit()
                return allocation is not None

        with ThreadPoolExecutor(max_workers=4) as workers:
            results = list(workers.map(claim, range(4)))
        assert 1 <= sum(results) <= 2
        with factory() as session:
            allocations = session.scalars(select(RuntimeAllocation)).all()
            assert len(allocations) == sum(results)
            assert len({allocation.execution_uid for allocation in allocations}) == len(allocations)
            for allocation in allocations:
                session.delete(allocation)  # No real process was started in this DB-only test.
            session.commit()
            manager = RuntimeManager(session, docker)
            manager.delete_runtime(session.get(WorkspaceRuntime, policy_ids[0]))
            remaining = session.get(WorkspaceRuntime, policy_ids[1])
            assert remaining.docker_container_id and not docker.removed
            assert remaining.host.resources["docker_volumes"] == [volume]
            manager.delete_runtime(remaining)
            assert len(docker.removed) == 1 and docker.removed_volumes == [volume]


def test_shared_host_migration_up_down_up():
    with _temporary_postgres_schema() as engine:
        config = Config("alembic.ini")
        config.set_main_option(
            "sqlalchemy.url", engine.url.render_as_string(hide_password=False).replace("%", "%%")
        )
        # The URL carries the test schema through the migration engine too.
        schema = engine.dialect.default_schema_name
        with engine.connect() as connection:
            schema = connection.scalar(text("SELECT current_schema()"))
        url = engine.url.update_query_dict({"options": f"-csearch_path={schema},public"})
        config.set_main_option(
            "sqlalchemy.url", url.render_as_string(hide_password=False).replace("%", "%%")
        )
        command.upgrade(config, "0117_shared_runtime_hosts")
        with sessionmaker(engine)() as session:
            user = User(email=f"{uuid4()}@migration.test", display_name="Migration owner")
            workspace = Workspace(owner=user, name="Migration", slug=uuid4().hex)
            session.add_all([user, workspace])
            session.flush()
            host_id = uuid4()
            deleted_id = uuid4()
            migration_workspace_id = workspace.id
            session.execute(
                text("""
                    INSERT INTO workspace_runtimes
                        (id, workspace_id, runtime_provider, runtime_type, name, status,
                         connection_status, execution_mode, pool_key, limits,
                         network_policy, capabilities, docker_container_id, created_at, updated_at)
                    VALUES (:id, :workspace, 'cloud_docker', 'docker', 'Old host',
                            'stopped', 'offline', 'persistent', 'old-pool', '{}', '{}',
                            '{}', 'old-container', now(), now())
                """),
                {"id": host_id, "workspace": workspace.id},
            )
            session.execute(
                text("""
                    INSERT INTO workspace_runtimes
                        (id, workspace_id, runtime_provider, runtime_type, name, status,
                         connection_status, execution_mode, limits, network_policy,
                         capabilities, docker_container_id, created_at, updated_at)
                    VALUES (:id, :workspace, 'cloud_docker', 'docker', 'Deleted host',
                            'deleted', 'offline', 'persistent', '{}', '{}', '{}',
                            'removed-container', now(), now())
                """),
                {"id": deleted_id, "workspace": workspace.id},
            )
            session.commit()
        command.upgrade(config, "0119_runtime_host_process_identity")
        with sessionmaker(engine)() as session:
            profile = AgentProfile(
                workspace_id=migration_workspace_id,
                name="Migrated model access",
                role="manager",
                runtime_policy={
                    "network": {
                        "mode": "restricted",
                        "proxy_url": "obsolete",
                        "gateway_network": "obsolete",
                        "allowed_domains": ["example.org"],
                        "allowed_ports": [443],
                    }
                },
            )
            session.add(profile)
            session.flush()
            session.add(
                AgentProfileVersion(
                    workspace_id=migration_workspace_id,
                    agent_profile_id=profile.id,
                    version=1,
                    snapshot={"runtime_policy": profile.runtime_policy},
                )
            )
            profile_id = profile.id
            session.commit()
        command.upgrade(config, "head")
        with sessionmaker(engine)() as session:
            profile = session.get(AgentProfile, profile_id)
            assert profile.runtime_policy["network"] == {
                "mode": "restricted",
                "allowed_domains": ["example.org"],
                "allowed_ports": [443],
            }
            version = session.scalar(
                select(AgentProfileVersion).where(
                    AgentProfileVersion.agent_profile_id == profile_id
                )
            )
            assert version.snapshot["runtime_policy"] == profile.runtime_policy
        with engine.connect() as connection:
            assert (
                connection.scalar(
                    text("SELECT execution_mode FROM workspace_runtimes WHERE id = :id"),
                    {"id": host_id},
                )
                == "shared"
            )
            assert connection.execute(
                text("SELECT status,host_id FROM workspace_runtimes WHERE id=:id"),
                {"id": deleted_id},
            ).one() == ("deleted", None)
            assert (
                connection.scalar(
                    text("SELECT count(*) FROM runtime_hosts WHERE id=:id"), {"id": deleted_id}
                )
                == 0
            )
            assert (
                connection.scalar(
                    text("SELECT host_id FROM workspace_runtimes WHERE id=:id"), {"id": host_id}
                )
                == host_id
            )
        assert "pool_key" not in {
            c["name"] for c in inspect(engine).get_columns("workspace_runtimes")
        }
        assert "runtime_allocations" in inspect(engine).get_table_names()
        assert "template_id" not in {
            c["name"] for c in inspect(engine).get_columns("mcp_deployments")
        }
        command.downgrade(config, "0116_execution_contracts")
        assert "runtime_allocations" not in inspect(engine).get_table_names()
        command.upgrade(config, "head")
        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT status,host_id FROM workspace_runtimes WHERE id=:id"),
                {"id": deleted_id},
            ).one() == ("deleted", None)
        assert "execution_pool_member_id" not in {
            c["name"] for c in inspect(engine).get_columns("workspace_runtimes")
        }
