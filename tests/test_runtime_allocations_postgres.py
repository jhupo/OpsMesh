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
                id=host_id, workspace_id=uuid4(), name="Unrelated", limits=host.limits
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
            session.execute(
                text("""
                    INSERT INTO workspace_runtimes
                        (id, workspace_id, runtime_provider, runtime_type, name, status,
                         connection_status, execution_mode, pool_key, limits,
                         network_policy, capabilities, created_at, updated_at)
                    VALUES (:id, :workspace, 'cloud_docker', 'docker', 'Old host',
                            'stopped', 'offline', 'persistent', 'old-pool', '{}', '{}',
                            '{}', now(), now())
                """),
                {"id": host_id, "workspace": workspace.id},
            )
            session.commit()
        command.upgrade(config, "head")
        with engine.connect() as connection:
            assert (
                connection.scalar(
                    text("SELECT execution_mode FROM workspace_runtimes WHERE id = :id"),
                    {"id": host_id},
                )
                == "shared"
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
        assert "execution_pool_member_id" not in {
            c["name"] for c in inspect(engine).get_columns("workspace_runtimes")
        }
